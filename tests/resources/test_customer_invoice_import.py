"""`CustomerInvoicesResource.import_invoice` (B2.3) — deux envois (pièce multipart puis import)
pour UN write de référence sous WAL : accusé 201+id, court-circuit idempotent avec relecture,
pièce refusée = FAILED (nouvel essai permis, zéro import), import 422 = FAILED, import 5xx =
IN_DOUBT puis doublon en doute, store WAL requis. Zéro réseau (respx)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from grandlivre import (
    CustomerInvoicesResource,
    PennylaneClient,
    PennylaneClientError,
    PennylaneServerError,
    WALDuplicatePayloadError,
    WALInDoubtError,
    WALStoreRequiredError,
)
from grandlivre.config import Settings
from grandlivre.wal import SqliteIdempotencyStore, WALState, compute_fingerprint

_BASE = "https://app.pennylane.com/api/external/v2"
_NS = "dossier-test"
_PDF = b"%PDF-1.7 stub facture\n"
_BODY = {
    "date": "2026-10-13",
    "deadline": "2026-11-12",
    "customer_id": 4201,
    "invoice_number": "F-2026-285",
    "currency": "EUR",
    "currency_amount_before_tax": "5000.00",
    "currency_amount": "6000.00",
    "currency_tax": "1000.00",
    "external_reference": "app:facture:F-2026-285",
    "invoice_lines": [
        {
            "label": "Prestation de conseil",
            "quantity": "10",
            "raw_currency_unit_price": "500",
            "unit": "jours",
            "currency_amount": "6000.00",
            "currency_tax": "1000.00",
            "vat_rate": "FR_200",
            "ledger_account_id": 4242,
        }
    ],
}
_CREEE = {
    "id": 777,
    "invoice_number": "F-2026-285",
    "external_reference": _BODY["external_reference"],
}


def _store(tmp_path: Path) -> SqliteIdempotencyStore:
    return SqliteIdempotencyStore(tmp_path / "wal.db")


def _fingerprint() -> str:
    import hashlib

    attendu = compute_fingerprint(
        {
            "method": "POST",
            "path": "/customer_invoices/import",
            "body": _BODY,
            "pdf_sha256": hashlib.sha256(_PDF).hexdigest(),
        }
    )
    assert CustomerInvoicesResource.import_fingerprint(_BODY, _PDF) == attendu
    return attendu


async def _importer(settings: Settings, store: SqliteIdempotencyStore | None):
    async with PennylaneClient(settings=settings) as client:
        return await CustomerInvoicesResource(client, wal_store=store).import_invoice(
            body=_BODY, pdf=_PDF, filename="facture-F-2026-285.pdf", namespace=_NS
        )


@respx.mock
async def test_import_confirme_piece_puis_facture(settings: Settings, tmp_path: Path) -> None:
    piece = respx.post(f"{_BASE}/file_attachments").respond(201, json={"id": 5})
    imp = respx.post(f"{_BASE}/customer_invoices/import").respond(201, json=_CREEE)
    ack = await _importer(settings, _store(tmp_path))
    assert (ack.status, ack.id, ack.file_attachment_id, ack.invoice_number) == (
        "confirmed",
        777,
        5,
        "F-2026-285",
    )
    assert piece.call_count == 1 and imp.call_count == 1
    depot = piece.calls.last.request
    assert depot.headers["content-type"].startswith("multipart/form-data")
    assert b'filename="facture-F-2026-285.pdf"' in depot.content and _PDF in depot.content
    assert "Authorization" in depot.headers
    corps = json.loads(imp.calls.last.request.content)
    assert corps == {**_BODY, "file_attachment_id": 5}
    entree = _store(tmp_path).get(_NS, _fingerprint())
    assert entree is not None and entree.state is WALState.CONFIRMED and entree.pennylane_id == 777


@respx.mock
async def test_rejeu_court_circuite_et_relit(settings: Settings, tmp_path: Path) -> None:
    respx.post(f"{_BASE}/file_attachments").respond(201, json={"id": 5})
    imp = respx.post(f"{_BASE}/customer_invoices/import").respond(201, json=_CREEE)
    relecture = respx.get(f"{_BASE}/customer_invoices/777").respond(200, json=_CREEE)
    store = _store(tmp_path)
    await _importer(settings, store)
    again = await _importer(settings, store)
    assert again.status == "already_confirmed" and again.id == 777
    assert again.invoice_number == "F-2026-285" and again.file_attachment_id is None
    assert imp.call_count == 1 and relecture.call_count == 1  # zéro nouvel envoi


@respx.mock
async def test_rejeu_facture_disparue_passe_en_doute(settings: Settings, tmp_path: Path) -> None:
    respx.post(f"{_BASE}/file_attachments").respond(201, json={"id": 5})
    respx.post(f"{_BASE}/customer_invoices/import").respond(201, json=_CREEE)
    respx.get(f"{_BASE}/customer_invoices/777").respond(404, json={"error": "not found"})
    store = _store(tmp_path)
    await _importer(settings, store)
    with pytest.raises(WALInDoubtError) as ei:
        await _importer(settings, store)
    assert ei.value.candidates == [777] and "external_reference" in ei.value.reason


@respx.mock
async def test_piece_refusee_aucun_import_et_nouvel_essai(
    settings: Settings, tmp_path: Path
) -> None:
    piece = respx.post(f"{_BASE}/file_attachments").mock(
        side_effect=[
            httpx.Response(422, json={"error": "unsupported"}),
            httpx.Response(201, json={"id": 6}),
        ]
    )
    imp = respx.post(f"{_BASE}/customer_invoices/import").respond(201, json=_CREEE)
    store = _store(tmp_path)
    with pytest.raises(PennylaneClientError):
        await _importer(settings, store)
    assert imp.call_count == 0
    entree = store.get(_NS, _fingerprint())
    assert entree is not None and entree.state is WALState.FAILED
    ack = await _importer(settings, store)  # FAILED n'est pas un doublon : on réessaie
    assert ack.status == "confirmed" and ack.file_attachment_id == 6
    assert piece.call_count == 2 and imp.call_count == 1


@respx.mock
async def test_import_422_failed(settings: Settings, tmp_path: Path) -> None:
    respx.post(f"{_BASE}/file_attachments").respond(201, json={"id": 5})
    respx.post(f"{_BASE}/customer_invoices/import").respond(
        422, json={"error": "invoice_number already taken"}
    )
    store = _store(tmp_path)
    with pytest.raises(PennylaneClientError):
        await _importer(settings, store)
    entree = store.get(_NS, _fingerprint())
    assert entree is not None and entree.state is WALState.FAILED


@respx.mock
async def test_import_5xx_en_doute_puis_doublon_en_doute(
    settings: Settings, tmp_path: Path
) -> None:
    respx.post(f"{_BASE}/file_attachments").respond(201, json={"id": 5})
    imp = respx.post(f"{_BASE}/customer_invoices/import").respond(503)
    store = _store(tmp_path)
    with pytest.raises(WALInDoubtError) as ei:
        await _importer(settings, store)
    assert ei.value.candidates == [] and "jamais de re-soumission" in ei.value.reason
    entree = store.get(_NS, ei.value.fingerprint)
    assert entree is not None and entree.state is WALState.IN_DOUBT
    with pytest.raises(WALDuplicatePayloadError) as ed:
        await _importer(settings, store)
    assert ed.value.wal_state is WALState.IN_DOUBT
    assert imp.call_count == 1  # jamais de re-POST après un doute


@respx.mock
async def test_import_reponse_sans_id_en_doute(settings: Settings, tmp_path: Path) -> None:
    respx.post(f"{_BASE}/file_attachments").respond(201, json={"id": 5})
    respx.post(f"{_BASE}/customer_invoices/import").respond(201, json={"label": "sans id"})
    with pytest.raises(WALInDoubtError):
        await _importer(settings, _store(tmp_path))


@respx.mock
async def test_piece_5xx_failed_pas_de_doute(settings: Settings, tmp_path: Path) -> None:
    respx.post(f"{_BASE}/file_attachments").respond(503)
    imp = respx.post(f"{_BASE}/customer_invoices/import").respond(201, json=_CREEE)
    store = _store(tmp_path)
    with pytest.raises(PennylaneServerError):
        await _importer(settings, store)
    entree = store.get(_NS, _fingerprint())
    assert entree is not None and entree.state is WALState.FAILED and imp.call_count == 0


async def test_store_wal_requis_et_attachment_interdit(settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(WALStoreRequiredError):
        await _importer(settings, None)
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(ValueError):
            await CustomerInvoicesResource(client, wal_store=_store(tmp_path)).import_invoice(
                body={**_BODY, "file_attachment_id": 1}, pdf=_PDF, filename="x.pdf", namespace=_NS
            )
