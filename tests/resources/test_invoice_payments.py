"""Tests de InvoicePaymentsResource — rapprochement de paiement et statut.

Profil 204-no-id (distinct de create() 201+id) : la garantie anti-doublon repose sur
``(namespace, fingerprint)`` du WAL ; ``confirm(None)`` (CONFIRMED sans pennylane_id —
l'index UNIQUE partiel SQLite autorise plusieurs NULL). Issues préservées :
204 → CONFIRMED · 4xx/422 → FAILED (relève) · 5xx/timeout → IN_DOUBT (jamais re-POST).

Fixtures groundées sur la VRAIE forme API (spec OpenAPI v2.0, capture réelle 2026-06-19) :
``POST /{supplier|customer}_invoices/{id}/matched_transactions`` body ``{transaction_id}``
→ 204 No Content.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from grandlivre import (
    InvoicePaymentsResource,
    PennylaneValidationError,
    WALDuplicatePayloadError,
    WALInDoubtError,
    WALStoreRequiredError,
)
from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.wal import SqliteIdempotencyStore, WALManager, WALState

_BASE = "https://app.pennylane.com/api/external/v2"
_NS = "dossier-test"


def _store(tmp_path: Path) -> SqliteIdempotencyStore:
    return SqliteIdempotencyStore(tmp_path / "wal.db")


@respx.mock
async def test_match_payment_supplier_confirmed(settings: Settings, tmp_path: Path) -> None:
    post = respx.post(f"{_BASE}/supplier_invoices/42/matched_transactions").respond(204)
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        ack = await InvoicePaymentsResource(client, wal_store=store).match_payment(
            invoice_kind="supplier", invoice_id=42, transaction_id=99, namespace=_NS
        )
    assert ack.status == "confirmed"
    assert ack.operation == "match_payment[supplier#42<-tx#99]"
    assert post.call_count == 1
    assert json.loads(post.calls.last.request.content) == {"transaction_id": 99}


@respx.mock
async def test_match_payment_customer_confirmed(settings: Settings, tmp_path: Path) -> None:
    post = respx.post(f"{_BASE}/customer_invoices/7/matched_transactions").respond(204)
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        ack = await InvoicePaymentsResource(client, wal_store=store).match_payment(
            invoice_kind="customer", invoice_id=7, transaction_id=5, namespace=_NS
        )
    assert ack.status == "confirmed"
    assert post.call_count == 1


@respx.mock
async def test_match_payment_idempotent_short_circuit(settings: Settings, tmp_path: Path) -> None:
    # Re-soumettre LA MÊME opération (même facture + même transaction) court-circuite :
    # 2ᵉ appel = already_confirmed, ZÉRO 2ᵉ POST.
    post = respx.post(f"{_BASE}/supplier_invoices/42/matched_transactions").respond(204)
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        first = await resource.match_payment(
            invoice_kind="supplier", invoice_id=42, transaction_id=99, namespace=_NS
        )
        second = await resource.match_payment(
            invoice_kind="supplier", invoice_id=42, transaction_id=99, namespace=_NS
        )
    assert first.status == "confirmed"
    assert second.status == "already_confirmed"
    assert post.call_count == 1  # le 2ᵉ appel n'a PAS re-POST


@respx.mock
async def test_match_payment_distinct_ops_both_confirm(settings: Settings, tmp_path: Path) -> None:
    # Cœur du design 204-no-id : deux opérations DISTINCTES confirment toutes deux avec
    # pennylane_id=None — l'index UNIQUE(namespace, pennylane_id) WHERE CONFIRMED de SQLite
    # autorise plusieurs NULL (NULLs distincts). Si le 2ᵉ confirm(None) était bloqué, ce
    # test échouerait bruyamment.
    p1 = respx.post(f"{_BASE}/supplier_invoices/42/matched_transactions").respond(204)
    p2 = respx.post(f"{_BASE}/supplier_invoices/43/matched_transactions").respond(204)
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        a1 = await resource.match_payment(
            invoice_kind="supplier", invoice_id=42, transaction_id=99, namespace=_NS
        )
        a2 = await resource.match_payment(
            invoice_kind="supplier", invoice_id=43, transaction_id=99, namespace=_NS
        )
    assert a1.status == "confirmed"
    assert a2.status == "confirmed"
    assert p1.call_count == 1
    assert p2.call_count == 1


@respx.mock
async def test_match_payment_422_failed_then_retryable(settings: Settings, tmp_path: Path) -> None:
    # 422 → PennylaneValidationError (FAILED déterministe, pas de retry). FAILED est
    # re-soumettable : un 2ᵉ appel re-POST et confirme.
    post = respx.post(f"{_BASE}/supplier_invoices/42/matched_transactions").mock(
        side_effect=[
            httpx.Response(422, json={"error": "transaction introuvable"}),
            httpx.Response(204),
        ]
    )
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        with pytest.raises(PennylaneValidationError):
            await resource.match_payment(
                invoice_kind="supplier", invoice_id=42, transaction_id=99, namespace=_NS
            )
        ack = await resource.match_payment(
            invoice_kind="supplier", invoice_id=42, transaction_id=99, namespace=_NS
        )
    assert ack.status == "confirmed"
    assert post.call_count == 2  # FAILED re-soumis → 2ᵉ POST


@respx.mock
async def test_match_payment_5xx_in_doubt_blocks_resubmit(
    settings: Settings, tmp_path: Path
) -> None:
    # 503 → état INCERTAIN → WALInDoubtError (jamais de re-POST auto). L'entrée reste
    # IN_DOUBT → une re-soumission est REFUSÉE (WALDuplicatePayloadError), zéro 2ᵉ POST.
    post = respx.post(f"{_BASE}/supplier_invoices/42/matched_transactions").respond(503)
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        with pytest.raises(WALInDoubtError) as ei:
            await resource.match_payment(
                invoice_kind="supplier", invoice_id=42, transaction_id=99, namespace=_NS
            )
        with pytest.raises(WALDuplicatePayloadError):
            await resource.match_payment(
                invoice_kind="supplier", invoice_id=42, transaction_id=99, namespace=_NS
            )
    entry = store.get(_NS, ei.value.fingerprint)
    assert entry is not None
    assert entry.state is WALState.IN_DOUBT
    assert post.call_count == 1  # un seul POST, aucun re-POST


@respx.mock
async def test_match_payment_connect_error_failed(settings: Settings, tmp_path: Path) -> None:
    # ConnectError = connexion JAMAIS établie → octets jamais envoyés → FAILED + relève.
    respx.post(f"{_BASE}/supplier_invoices/42/matched_transactions").mock(
        side_effect=httpx.ConnectError("connexion refusée")
    )
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        with pytest.raises(httpx.ConnectError):
            await resource.match_payment(
                invoice_kind="supplier", invoice_id=42, transaction_id=99, namespace=_NS
            )
        # FAILED re-soumettable : un re-essai (réseau rétabli) re-POST et confirme.
        respx.post(f"{_BASE}/supplier_invoices/42/matched_transactions").respond(204)
        ack = await resource.match_payment(
            invoice_kind="supplier", invoice_id=42, transaction_id=99, namespace=_NS
        )
    assert ack.status == "confirmed"


@respx.mock
async def test_match_payment_requires_real_store(settings: Settings, tmp_path: Path) -> None:
    # STORE-1 : write sans store WAL réel → refus fail-fast AVANT tout POST.
    post = respx.post(f"{_BASE}/supplier_invoices/42/matched_transactions").respond(204)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client)  # wal_store=None
        with pytest.raises(WALStoreRequiredError):
            await resource.match_payment(
                invoice_kind="supplier", invoice_id=42, transaction_id=99, namespace=_NS
            )
    assert post.call_count == 0


# ─── update_payment_status (fournisseur) + mark_customer_invoice_paid (client) ──


@respx.mock
async def test_update_payment_status_paid_confirmed(settings: Settings, tmp_path: Path) -> None:
    put = respx.put(f"{_BASE}/supplier_invoices/42/payment_status").respond(204)
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        ack = await InvoicePaymentsResource(client, wal_store=store).update_payment_status(
            invoice_id=42, payment_status="paid", namespace=_NS
        )
    assert ack.status == "confirmed"
    assert put.call_count == 1
    assert json.loads(put.calls.last.request.content) == {"payment_status": "paid"}


@respx.mock
async def test_update_payment_status_to_be_paid_confirmed(
    settings: Settings, tmp_path: Path
) -> None:
    put = respx.put(f"{_BASE}/supplier_invoices/42/payment_status").respond(204)
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        ack = await InvoicePaymentsResource(client, wal_store=store).update_payment_status(
            invoice_id=42, payment_status="to_be_paid", namespace=_NS
        )
    assert ack.status == "confirmed"
    assert json.loads(put.calls.last.request.content) == {"payment_status": "to_be_paid"}


@respx.mock
async def test_mark_customer_invoice_paid_confirmed(settings: Settings, tmp_path: Path) -> None:
    # mark_as_paid : PUT SANS corps (requestBody non requis, spec OpenAPI v2.0).
    put = respx.put(f"{_BASE}/customer_invoices/7/mark_as_paid").respond(204)
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        ack = await InvoicePaymentsResource(client, wal_store=store).mark_customer_invoice_paid(
            invoice_id=7, namespace=_NS
        )
    assert ack.status == "confirmed"
    assert put.call_count == 1
    assert put.calls.last.request.content == b""  # aucun corps envoyé


@respx.mock
async def test_update_payment_status_idempotent_short_circuit(
    settings: Settings, tmp_path: Path
) -> None:
    """F-1 : le court-circuit relit la facture ; état conforme → zéro renvoi, vérifié."""
    put = respx.put(f"{_BASE}/supplier_invoices/42/payment_status").respond(204)
    get = respx.get(f"{_BASE}/supplier_invoices/42").respond(
        200, json={"id": 42, "payment_status": "paid_offline"}
    )
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        first = await resource.update_payment_status(
            invoice_id=42, payment_status="paid", namespace=_NS
        )
        second = await resource.update_payment_status(
            invoice_id=42, payment_status="paid", namespace=_NS
        )
    assert first.status == "confirmed"
    assert first.state_verified is False and first.observed_state is None
    assert second.status == "already_confirmed"
    assert second.state_verified is True
    assert second.observed_state == "paid_offline"
    assert put.call_count == 1
    assert get.call_count == 1


@respx.mock
async def test_update_payment_status_flip_flop_resends_once(
    settings: Settings, tmp_path: Path
) -> None:
    """F-1 : facture repassée « à payer » côté Pennylane après un « payée » confirmé ici →
    la relecture contredit le mapping → invalidation (sans id) + UN renvoi du PUT."""
    put = respx.put(f"{_BASE}/supplier_invoices/42/payment_status").respond(204)
    get = respx.get(f"{_BASE}/supplier_invoices/42").respond(
        200, json={"id": 42, "payment_status": "to_be_paid"}
    )
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        first = await resource.update_payment_status(
            invoice_id=42, payment_status="paid", namespace=_NS
        )
        second = await resource.update_payment_status(
            invoice_id=42, payment_status="paid", namespace=_NS
        )
    assert first.status == "confirmed"
    assert second.status == "confirmed"
    assert second.observed_state == "to_be_paid"
    assert "renvoyé" in second.detail
    assert put.call_count == 2
    assert get.call_count == 1
    # Le mapping est re-confirmé (sans id) : plus rien en vol, un 3ᵉ appel identique
    # court-circuiterait à nouveau (et re-sonderait).
    assert store.iter_nonterminal() == []


@respx.mock
async def test_update_payment_status_short_circuit_ambiguous_state_no_resend(
    settings: Settings, tmp_path: Path
) -> None:
    """F-1 : paiement en cours / partiel = état ambigu → aucun renvoi automatique."""
    put = respx.put(f"{_BASE}/supplier_invoices/42/payment_status").respond(204)
    respx.get(f"{_BASE}/supplier_invoices/42").respond(
        200, json={"id": 42, "payment_status": "payment_in_progress"}
    )
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        await resource.update_payment_status(invoice_id=42, payment_status="paid", namespace=_NS)
        second = await resource.update_payment_status(
            invoice_id=42, payment_status="paid", namespace=_NS
        )
    assert second.status == "already_confirmed"
    assert second.state_verified is False
    assert second.observed_state == "payment_in_progress"
    assert "ambigu" in second.detail
    assert put.call_count == 1


@respx.mock
async def test_update_payment_status_short_circuit_probe_failure_no_resend(
    settings: Settings, tmp_path: Path
) -> None:
    """F-1 : relecture en échec (5xx épuisé) → conservateur : zéro renvoi, non vérifié."""
    put = respx.put(f"{_BASE}/supplier_invoices/42/payment_status").respond(204)
    respx.get(f"{_BASE}/supplier_invoices/42").respond(503)
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        await resource.update_payment_status(invoice_id=42, payment_status="paid", namespace=_NS)
        second = await resource.update_payment_status(
            invoice_id=42, payment_status="paid", namespace=_NS
        )
    assert second.status == "already_confirmed"
    assert second.state_verified is False
    assert second.observed_state is None
    assert "invérifiable" in second.detail
    assert put.call_count == 1


@respx.mock
async def test_mark_customer_invoice_paid_short_circuit_probes_paid_flag(
    settings: Settings, tmp_path: Path
) -> None:
    """F-1 côté client : ``paid=true`` → vérifié sans renvoi ; ``paid=false`` → renvoi."""
    put = respx.put(f"{_BASE}/customer_invoices/7/mark_as_paid").respond(204)
    get = respx.get(f"{_BASE}/customer_invoices/7").respond(
        200, json={"id": 7, "paid": True, "status": "paid"}
    )
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        await resource.mark_customer_invoice_paid(invoice_id=7, namespace=_NS)
        verified = await resource.mark_customer_invoice_paid(invoice_id=7, namespace=_NS)
        get.respond(200, json={"id": 7, "paid": False, "status": "late"})
        resent = await resource.mark_customer_invoice_paid(invoice_id=7, namespace=_NS)
    assert verified.status == "already_confirmed" and verified.state_verified is True
    assert verified.observed_state == "paid=true"
    assert resent.status == "confirmed"
    assert put.call_count == 2


@respx.mock
async def test_match_payment_short_circuit_has_no_state_probe(
    settings: Settings, tmp_path: Path
) -> None:
    """F-1 option (b) pour le rapprochement : aucune relecture, « déjà envoyé » historique."""
    post = respx.post(f"{_BASE}/supplier_invoices/42/matched_transactions").respond(204)
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        await resource.match_payment(
            invoice_kind="supplier", invoice_id=42, transaction_id=99, namespace=_NS
        )
        second = await resource.match_payment(
            invoice_kind="supplier", invoice_id=42, transaction_id=99, namespace=_NS
        )
    assert second.status == "already_confirmed"
    assert second.state_verified is False and second.observed_state is None
    assert "historique" in second.detail
    assert post.call_count == 1


@respx.mock
async def test_update_payment_status_distinct_values_not_deduped(
    settings: Settings, tmp_path: Path
) -> None:
    # paid puis to_be_paid sur la MÊME facture = empreintes distinctes (body différent) →
    # deux PUT, deux CONFIRMED à pennylane_id NULL (toggle de statut légitime).
    put = respx.put(f"{_BASE}/supplier_invoices/42/payment_status").respond(204)
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        a1 = await resource.update_payment_status(
            invoice_id=42, payment_status="paid", namespace=_NS
        )
        a2 = await resource.update_payment_status(
            invoice_id=42, payment_status="to_be_paid", namespace=_NS
        )
    assert a1.status == "confirmed"
    assert a2.status == "confirmed"
    assert put.call_count == 2


# ─── recover (reprise au démarrage, posture store/recover) ─────────────────────


async def test_recover_in_flight_to_in_doubt_scoped(settings: Settings, tmp_path: Path) -> None:
    # Un IN_FLIGHT orphelin (crash en vol) du dossier courant → IN_DOUBT (vérif humaine,
    # jamais de re-POST). Un IN_FLIGHT d'un AUTRE dossier est laissé intact (multi-dossiers :
    # repris par son propre client). Aucun appel HTTP (recover ne touche que le WAL).
    store = _store(tmp_path)
    store.reserve_in_flight(_NS, "fp-target-inflight", "idem-1")
    store.reserve_in_flight("autre-dossier", "fp-other-inflight", "idem-2")
    # F2 : entrée LEDGER (readback_key présent) dans le dossier courant → le recover PAIEMENT
    # ne doit PAS y toucher (gérée par LedgerEntriesResource.recover ; store WAL partagé).
    store.reserve_in_flight(
        _NS,
        "fp-ledger-inflight",
        "idem-3",
        readback_key={"date": "2026-01-01", "journal_id": 1, "line_signature": []},
    )
    async with PennylaneClient(settings=settings) as client:
        report = await InvoicePaymentsResource(client, wal_store=store).recover(namespace=_NS)
    target = store.get(_NS, "fp-target-inflight")
    other = store.get("autre-dossier", "fp-other-inflight")
    ledger = store.get(_NS, "fp-ledger-inflight")
    assert target is not None and target.state is WALState.IN_DOUBT
    assert other is not None and other.state is WALState.IN_FLIGHT  # hors scope, intact
    assert ledger is not None and ledger.state is WALState.IN_FLIGHT  # F2 : ledger non traité
    assert [r["fingerprint"] for r in report] == ["fp-target-inflight"]  # seul le paiement


# ─── F1 (review locale) : resolve(landed=True) sans id pour les writes 204-no-id ──


async def test_resolve_landed_no_id_ok_for_payment_blocked_for_ledger(
    tmp_path: Path,
) -> None:
    # F1 : un write paiement (204-no-id, readback_key None) passé en IN_DOUBT (timeout/5xx)
    # DOIT pouvoir être confirmé par l'humain via resolve(landed=True, pennylane_id=None) —
    # sinon la seule issue est FAILED → re-POST → double rapprochement/statut. Une écriture
    # ledger (readback_key présent) exige toujours l'id (un CONFIRMED sans id y serait fantôme).
    store = _store(tmp_path)
    mgr = WALManager(store)

    # Entrée PAIEMENT IN_DOUBT (aucun readback_key).
    store.reserve_in_flight(_NS, "fp-paiement", "idem-pay")
    store.finalize(_NS, "fp-paiement", WALState.IN_DOUBT, None, "doute technique")
    entry = await mgr.resolve(
        namespace=_NS, fingerprint="fp-paiement", landed=True, pennylane_id=None
    )
    assert entry.state is WALState.CONFIRMED  # confirmé sans id (ack-only) — F1

    # Entrée LEDGER IN_DOUBT (readback_key présent) → id toujours exigé.
    store.reserve_in_flight(
        _NS,
        "fp-ledger",
        "idem-ledger",
        readback_key={"date": "2026-01-01", "journal_id": 1, "line_signature": []},
    )
    store.finalize(_NS, "fp-ledger", WALState.IN_DOUBT, None, "doute technique")
    with pytest.raises(ValueError):
        await mgr.resolve(namespace=_NS, fingerprint="fp-ledger", landed=True, pennylane_id=None)


@respx.mock
async def test_update_payment_status_stale_twice_raises_in_doubt(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-1 borne de rejeu : si le mapping reste CONFIRMED-périmé après l'invalidation
    (concurrent qui l'a re-confirmé entre-temps), la 2ᵉ passe ne renvoie pas en boucle :
    ``WALInDoubtError`` → vérification humaine."""
    put = respx.put(f"{_BASE}/supplier_invoices/42/payment_status").respond(204)
    respx.get(f"{_BASE}/supplier_invoices/42").respond(
        200, json={"id": 42, "payment_status": "to_be_paid"}
    )
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        await resource.update_payment_status(invoice_id=42, payment_status="paid", namespace=_NS)

        async def _invalidate_lost_race(*args: object, **kwargs: object) -> bool:
            return False  # mapping « changé entre-temps » : rien transitionné

        monkeypatch.setattr(resource._get_wal(), "invalidate_confirmed", _invalidate_lost_race)
        with pytest.raises(WALInDoubtError, match="toujours contraire"):
            await resource.update_payment_status(
                invoice_id=42, payment_status="paid", namespace=_NS
            )
    assert put.call_count == 1


@respx.mock
async def test_mark_customer_invoice_paid_probe_without_flag_is_unverifiable(
    settings: Settings, tmp_path: Path
) -> None:
    put = respx.put(f"{_BASE}/customer_invoices/7/mark_as_paid").respond(204)
    respx.get(f"{_BASE}/customer_invoices/7").respond(200, json={"id": 7, "status": "late"})
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        await resource.mark_customer_invoice_paid(invoice_id=7, namespace=_NS)
        second = await resource.mark_customer_invoice_paid(invoice_id=7, namespace=_NS)
    assert second.status == "already_confirmed"
    assert second.state_verified is False and second.observed_state is None
    assert put.call_count == 1


@respx.mock
async def test_update_payment_status_paid_flag_counts_as_paid(
    settings: Settings, tmp_path: Path
) -> None:
    """F-1 : réglée par rapprochement (``paid=true``, ``payment_status`` inchangé) →
    « payée » est conforme sans renvoi ; « à payer » est contradictoire → ambigu, zéro envoi."""
    put = respx.put(f"{_BASE}/supplier_invoices/42/payment_status").respond(204)
    respx.get(f"{_BASE}/supplier_invoices/42").respond(
        200, json={"id": 42, "payment_status": "to_be_processed", "paid": True}
    )
    store = _store(tmp_path)
    async with PennylaneClient(settings=settings) as client:
        resource = InvoicePaymentsResource(client, wal_store=store)
        await resource.update_payment_status(invoice_id=42, payment_status="paid", namespace=_NS)
        again = await resource.update_payment_status(
            invoice_id=42, payment_status="paid", namespace=_NS
        )
        await resource.update_payment_status(
            invoice_id=42, payment_status="to_be_paid", namespace=_NS
        )
        contradictory = await resource.update_payment_status(
            invoice_id=42, payment_status="to_be_paid", namespace=_NS
        )
    assert again.status == "already_confirmed" and again.state_verified is True
    assert again.observed_state == "to_be_processed (paid=true)"
    assert contradictory.status == "already_confirmed" and contradictory.state_verified is False
    assert "ambigu" in contradictory.detail
    assert put.call_count == 2  # un envoi par opération distincte, aucun renvoi
