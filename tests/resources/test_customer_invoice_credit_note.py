"""`CustomerInvoicesResource.import_e_invoice` et `link_credit_note` (B2.6, avoir chez l'éditeur) —
chacun UN write de référence sous WAL : import « facture électronique » (multipart : le PDF
Factur-X et les options JSON), accusé 201+id (traitement différé chez l'éditeur), court-circuit
relu, 422 = FAILED, 5xx = IN_DOUBT puis doublon en doute ; rattachement avoir → facture (200),
court-circuit sans envoi, 422 = FAILED, 5xx = IN_DOUBT. Zéro réseau (respx). Formes réelles :
sonde du 20/10/2026 sur le bac à sable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
import respx

from grandlivre import (
    CreditNoteLinkAck,
    CustomerInvoicesResource,
    PennylaneClient,
    PennylaneClientError,
    WALDuplicatePayloadError,
    WALInDoubtError,
    WALStoreRequiredError,
)
from grandlivre.config import Settings
from grandlivre.wal import SqliteIdempotencyStore, WALState, compute_fingerprint

_BASE = "https://app.pennylane.com/api/external/v2"
_NS = "dossier-test"
_PDF = b"%PDF-1.7 stub avoir factur-x\n"
_OPTIONS = {
    "customer_id": 1469093801984,
    "invoice_lines": [{"e_invoice_line_id": "1", "ledger_account_id": 4242}],
}
_IMPORT = f"{_BASE}/customer_invoices/e_invoices/imports"
_LINK = f"{_BASE}/customer_invoices/29099542224896/link_credit_note"


def _store(tmp_path: Path) -> SqliteIdempotencyStore:
    return SqliteIdempotencyStore(tmp_path / "wal.db")


async def _importer(settings: Settings, store: SqliteIdempotencyStore | None, **kw: object):
    params: dict[str, object] = {
        "pdf": _PDF,
        "filename": "avoir-A-1.pdf",
        "namespace": _NS,
        "invoice_options": _OPTIONS,
    }
    params.update(kw)
    async with PennylaneClient(settings=settings) as client:
        return await CustomerInvoicesResource(client, wal_store=store).import_e_invoice(**params)  # type: ignore[arg-type]


async def _rattacher(settings: Settings, store: SqliteIdempotencyStore | None) -> CreditNoteLinkAck:
    async with PennylaneClient(settings=settings) as client:
        return await CustomerInvoicesResource(client, wal_store=store).link_credit_note(
            invoice_id=29099542224896, credit_note_id=32654516539392, namespace=_NS
        )


def test_empreinte_import_e_invoice() -> None:
    attendu = compute_fingerprint(
        {
            "method": "POST",
            "path": "/customer_invoices/e_invoices/imports",
            "invoice_options": _OPTIONS,
            "pdf_sha256": hashlib.sha256(_PDF).hexdigest(),
        }
    )
    assert CustomerInvoicesResource.e_invoice_import_fingerprint(_OPTIONS, _PDF) == attendu
    assert CustomerInvoicesResource.e_invoice_import_fingerprint(None, _PDF) != attendu


@respx.mock
async def test_import_e_invoice_multipart_fichier_et_options(
    settings: Settings, tmp_path: Path
) -> None:
    imp = respx.post(_IMPORT).respond(
        201, json={"id": 32654520766464, "url": "https://…/32654520766464"}
    )
    store = _store(tmp_path)
    ack = await _importer(settings, store)
    assert (ack.status, ack.id, ack.file_attachment_id) == ("confirmed", 32654520766464, None)
    assert "différé" in ack.detail
    requete = imp.calls.last.request
    assert requete.headers["content-type"].startswith("multipart/form-data")
    assert b'name="file"; filename="avoir-A-1.pdf"' in requete.content and _PDF in requete.content
    assert b'name="invoice_options"' in requete.content
    options = json.dumps(_OPTIONS, separators=(",", ":")).encode("utf-8")
    assert options in requete.content and "Authorization" in requete.headers
    entree = store.get(_NS, CustomerInvoicesResource.e_invoice_import_fingerprint(_OPTIONS, _PDF))
    assert entree is not None and entree.state is WALState.CONFIRMED
    assert entree.pennylane_id == 32654520766464


@respx.mock
async def test_import_e_invoice_sans_options_n_envoie_que_le_fichier(
    settings: Settings, tmp_path: Path
) -> None:
    imp = respx.post(_IMPORT).respond(201, json={"id": 9})
    await _importer(settings, _store(tmp_path), invoice_options=None)
    assert b'name="invoice_options"' not in imp.calls.last.request.content


@respx.mock
async def test_import_e_invoice_rejeu_court_circuite_et_relit(
    settings: Settings, tmp_path: Path
) -> None:
    imp = respx.post(_IMPORT).respond(201, json={"id": 9})
    relu = respx.get(f"{_BASE}/customer_invoices/9").respond(
        200, json={"id": 9, "invoice_number": "A-1"}
    )
    store = _store(tmp_path)
    await _importer(settings, store)
    again = await _importer(settings, store)
    assert (again.status, again.id, again.invoice_number) == ("already_confirmed", 9, "A-1")
    assert imp.call_count == 1 and relu.call_count == 1  # zéro nouvel envoi
    respx.get(f"{_BASE}/customer_invoices/9").respond(404, json={"error": "not found"})
    with pytest.raises(WALInDoubtError) as ei:
        await _importer(settings, store)
    assert ei.value.candidates == [9] and "numéro de facture" in ei.value.reason


@respx.mock
async def test_import_e_invoice_422_failed_puis_nouvel_essai(
    settings: Settings, tmp_path: Path
) -> None:
    imp = respx.post(_IMPORT).mock(
        side_effect=[
            httpx.Response(422, json={"error": "invalid e-invoice"}),
            httpx.Response(201, json={"id": 10}),
        ]
    )
    store = _store(tmp_path)
    with pytest.raises(PennylaneClientError):
        await _importer(settings, store)
    entree = store.get(_NS, CustomerInvoicesResource.e_invoice_import_fingerprint(_OPTIONS, _PDF))
    assert entree is not None and entree.state is WALState.FAILED
    ack = await _importer(settings, store)
    assert ack.status == "confirmed" and ack.id == 10 and imp.call_count == 2


@respx.mock
async def test_import_e_invoice_5xx_en_doute_jamais_de_repost(
    settings: Settings, tmp_path: Path
) -> None:
    imp = respx.post(_IMPORT).respond(502)
    store = _store(tmp_path)
    with pytest.raises(WALInDoubtError) as ei:
        await _importer(settings, store)
    assert "jamais de re-soumission" in ei.value.reason
    with pytest.raises(WALDuplicatePayloadError) as ed:
        await _importer(settings, store)
    assert ed.value.wal_state is WALState.IN_DOUBT and imp.call_count == 1
    respx.post(_IMPORT).respond(201, json={"sans": "id"})
    with pytest.raises(WALInDoubtError):
        await _importer(settings, _store(tmp_path / "b"), filename="autre.pdf", pdf=b"%PDF autre")


@respx.mock
async def test_link_credit_note_confirme_puis_court_circuit(
    settings: Settings, tmp_path: Path
) -> None:
    lien = respx.post(_LINK).respond(
        200,
        json={
            "id": 29099542224896,
            "status": "partially_cancelled",
            "remaining_amount_with_tax": "1728.0",
        },
    )
    store = _store(tmp_path)
    ack = await _rattacher(settings, store)
    assert (ack.status, ack.invoice_status, ack.remaining_amount_with_tax) == (
        "confirmed",
        "partially_cancelled",
        "1728.0",
    )
    assert json.loads(lien.calls.last.request.content) == {"credit_note_id": 32654516539392}
    again = await _rattacher(settings, store)
    assert again.status == "already_confirmed" and lien.call_count == 1


@respx.mock
async def test_link_credit_note_422_failed_et_5xx_en_doute(
    settings: Settings, tmp_path: Path
) -> None:
    respx.post(_LINK).respond(422, json={"error": "credit note already linked"})
    with pytest.raises(PennylaneClientError):
        await _rattacher(settings, _store(tmp_path))
    respx.post(_LINK).respond(503)
    with pytest.raises(WALInDoubtError) as ei:
        await _rattacher(settings, _store(tmp_path / "b"))
    assert ei.value.candidates == [32654516539392] and "credited_invoice" in ei.value.reason


async def test_store_wal_requis(settings: Settings) -> None:
    with pytest.raises(WALStoreRequiredError):
        await _importer(settings, None)
    with pytest.raises(WALStoreRequiredError):
        await _rattacher(settings, None)
