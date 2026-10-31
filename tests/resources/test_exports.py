"""Tests de FECExportResource (v0.3 B.6).

**Pattern asynchrone POST + polling GET + download** (obligation
légale française CGI 286 I bis).

**Bloqueur scope sandbox** : Company Token actuel n'a pas le scope
`ledger` requis pour POST `/exports/fecs` (HTTP 403 attendu, dérivé
du cas B.7 `transactions:readonly` cf.
`tests/resources/test_transactions.py:57-77`). Tests respx mockent
les réponses — pas de smoke sandbox tant que scope absent.

Schéma sourcé `openapi_spec_raw.json` zones
`paths./api/external/v2/exports/fecs` (POST 201) et
`paths./api/external/v2/exports/fecs/{id}` (GET 200).

L'enum `status` est strictement `pending | ready | error` (PAS
`completed/failed/running` — sourcé openapi spec brut).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import (
    FECExportFailedError,
    PennylaneAuthError,
)
from grandlivre.models.exports import (
    AnalyticalGeneralLedgerExportJob,
    FECExportJob,
    GeneralLedgerExportJob,
)
from grandlivre.resources.exports import (
    AnalyticalGeneralLedgerExportResource,
    FECExportResource,
    GeneralLedgerExportResource,
)

_BASE = "https://app.pennylane.com/api/external/v2"


def _job_pending(jid: int = 124) -> dict:
    """Fixture POST 201 / GET pending — sans file_url."""
    return {
        "id": jid,
        "status": "pending",
        "created_at": "2026-05-12T10:00:00Z",
        "updated_at": "2026-05-12T10:00:00Z",
    }


def _job_ready(jid: int = 124, file_url: str = "https://files.pennylane/fec.txt") -> dict:
    return {
        "id": jid,
        "status": "ready",
        "created_at": "2026-05-12T10:00:00Z",
        "updated_at": "2026-05-12T10:00:05Z",
        "file_url": file_url,
    }


def _job_error(jid: int = 124) -> dict:
    return {
        "id": jid,
        "status": "error",
        "created_at": "2026-05-12T10:00:00Z",
        "updated_at": "2026-05-12T10:00:05Z",
        "file_url": None,
    }


# ─── create() POST ───────────────────────────────────────────────────────────


@respx.mock
async def test_create_post_returns_pending_job(settings: Settings) -> None:
    """POST OK → FECExportJob avec status='pending' (cas nominal API)."""
    route = respx.post(f"{_BASE}/exports/fecs").respond(201, json=_job_pending(124))
    async with PennylaneClient(settings=settings) as client:
        job = await FECExportResource(client).create(
            period_start="2026-01-01", period_end="2026-01-31"
        )
    assert isinstance(job, FECExportJob)
    assert job.id == 124
    assert job.status == "pending"
    assert job.file_url is None
    # Body envoyé conforme à la spec OpenAPI (2 champs requis).
    assert route.called
    sent = route.calls.last.request
    import json as _json

    body = _json.loads(sent.content)
    assert body == {"period_start": "2026-01-01", "period_end": "2026-01-31"}


# ─── status() GET ────────────────────────────────────────────────────────────


@respx.mock
async def test_status_get_returns_updated_job(settings: Settings) -> None:
    respx.get(f"{_BASE}/exports/fecs/124").respond(200, json=_job_ready(124))
    async with PennylaneClient(settings=settings) as client:
        job = await FECExportResource(client).status(124)
    assert job.id == 124
    assert job.status == "ready"
    assert job.file_url == "https://files.pennylane/fec.txt"


# ─── wait() polling success ──────────────────────────────────────────────────


@respx.mock
async def test_wait_polling_pending_then_ready(settings: Settings) -> None:
    """3 GET successifs : pending → pending → ready (terminal)."""
    respx.get(f"{_BASE}/exports/fecs/124").mock(
        side_effect=[
            httpx.Response(200, json=_job_pending(124)),
            httpx.Response(200, json=_job_pending(124)),
            httpx.Response(200, json=_job_ready(124)),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        job = await FECExportResource(client).wait(124, poll_interval=0.01, timeout=10.0)
    assert job.status == "ready"
    assert job.file_url == "https://files.pennylane/fec.txt"


# ─── wait() polling timeout ──────────────────────────────────────────────────


@respx.mock
async def test_wait_polling_timeout(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pending indéfini → TimeoutError quand deadline dépassée.

    `asyncio.sleep` neutralisé par autouse fixture conftest. Le
    `time.monotonic()` est monkeypatché pour avancer artificiellement
    et déclencher le timeout.
    """
    respx.get(f"{_BASE}/exports/fecs/124").respond(200, json=_job_pending(124))

    # Compteur monotonic incrémenté à chaque appel.
    counter = {"t": 0.0}

    def _fake_monotonic() -> float:
        counter["t"] += 0.6  # > timeout après quelques itérations
        return counter["t"]

    monkeypatch.setattr(
        "grandlivre.resources.exports.time.monotonic",
        _fake_monotonic,
    )

    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(TimeoutError) as excinfo:
            await FECExportResource(client).wait(124, poll_interval=0.01, timeout=1.0)
    assert "124" in str(excinfo.value)
    assert "pending" in str(excinfo.value)


# ─── wait() polling failed ───────────────────────────────────────────────────


@respx.mock
async def test_wait_polling_error_raises_failed(settings: Settings) -> None:
    """status='error' → FECExportFailedError immédiate."""
    respx.get(f"{_BASE}/exports/fecs/124").respond(200, json=_job_error(124))
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(FECExportFailedError) as excinfo:
            await FECExportResource(client).wait(124, poll_interval=0.01, timeout=10.0)
    assert "124" in str(excinfo.value)
    assert "error" in str(excinfo.value)


# ─── download() ──────────────────────────────────────────────────────────────


@respx.mock
async def test_download_returns_file_bytes(settings: Settings) -> None:
    """Job ready → GET file_url externe → bytes."""
    fec_content = b"JournalCode|JournalLib|EcritureNum|...\nVE|Ventes|1|...\n"
    file_url = "https://files.pennylane/my_export.txt"
    respx.get(f"{_BASE}/exports/fecs/124").respond(200, json=_job_ready(124, file_url=file_url))
    respx.get(file_url).respond(200, content=fec_content)

    async with PennylaneClient(settings=settings) as client:
        payload = await FECExportResource(client).download(124)
    assert payload == fec_content
    assert isinstance(payload, bytes)


@respx.mock
async def test_download_follows_302_redirect_to_s3(settings: Settings) -> None:
    """Réel 2026-09-16 : `file_url` (active_storage) répond 302 vers S3 ; sans suivi,
    `raise_for_status` levait. Le client dédié suit la redirection (lot 0 B0.2)."""
    fec_content = b"JournalCode\tJournalLib\tEcritureNum\n"
    file_url = "https://app.pennylane.com/rails/active_storage/blobs/redirect/abc/fec.txt"
    s3_url = "https://jeancaisse-20190924.s3.eu-west-1.amazonaws.com/fec.txt?X-Amz-Signature=x"
    respx.get(f"{_BASE}/exports/fecs/124").respond(200, json=_job_ready(124, file_url=file_url))
    respx.get(file_url).respond(302, headers={"Location": s3_url})
    s3 = respx.get(s3_url).respond(200, content=fec_content)

    async with PennylaneClient(settings=settings) as client:
        payload = await FECExportResource(client).download(124)
    assert payload == fec_content
    assert s3.called
    # Aucun Bearer Pennylane ne fuit vers l'hôte externe.
    assert "authorization" not in {k.lower() for k in s3.calls.last.request.headers}


@respx.mock
async def test_download_raises_when_file_url_missing(settings: Settings) -> None:
    """status='pending' (file_url None) → FECExportFailedError."""
    respx.get(f"{_BASE}/exports/fecs/124").respond(200, json=_job_pending(124))
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(FECExportFailedError) as excinfo:
            await FECExportResource(client).download(124)
    assert "file_url" in str(excinfo.value)


# ─── Propagation 401 ─────────────────────────────────────────────────────────


@respx.mock
async def test_create_propagates_401_auth_error(settings: Settings) -> None:
    respx.post(f"{_BASE}/exports/fecs").respond(
        401, json={"error": "The access token is invalid", "status": 401}
    )
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError) as excinfo:
            await FECExportResource(client).create(
                period_start="2026-01-01", period_end="2026-01-31"
            )
    assert excinfo.value.status_code == 401


# ─── Propagation 403 (scope `ledger` manquant — cas sandbox actuel) ──────────


@respx.mock
async def test_create_propagates_403_when_scope_missing(settings: Settings) -> None:
    """Cas reproductible empiriquement sandbox 2026-05-12 : Company
    Token sans scope `ledger` → HTTP 403 sur POST `/exports/fecs`.
    """
    respx.post(f"{_BASE}/exports/fecs").respond(
        403,
        json={
            "status": 403,
            "error": 'Access to this resource requires scope "ledger".',
        },
    )
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError) as excinfo:
            await FECExportResource(client).create(
                period_start="2026-01-01", period_end="2026-01-31"
            )
    assert excinfo.value.status_code == 403


# ─── Résilience extra="allow" + payload minimal ──────────────────────────────


@respx.mock
async def test_extra_fields_preserved(settings: Settings) -> None:
    """Champs inattendus préservés (résilience évolution spec)."""
    payload = {
        **_job_ready(124),
        "billable_amount": "12.50",  # champ inattendu
        "internal_metadata": {"trace_id": "abc"},
    }
    respx.get(f"{_BASE}/exports/fecs/124").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        job = await FECExportResource(client).status(124)
    dumped = job.model_dump()
    assert dumped["billable_amount"] == "12.50"
    assert dumped["internal_metadata"] == {"trace_id": "abc"}


@respx.mock
async def test_minimal_payload_post_201(settings: Settings) -> None:
    """POST 201 schéma minimal : id+status+created_at+updated_at,
    file_url absent (pas encore prêt)."""
    minimal = {
        "id": 999,
        "status": "pending",
        "created_at": "2026-05-12T10:00:00Z",
        "updated_at": "2026-05-12T10:00:00Z",
    }
    respx.post(f"{_BASE}/exports/fecs").respond(201, json=minimal)
    async with PennylaneClient(settings=settings) as client:
        job = await FECExportResource(client).create(
            period_start="2026-01-01", period_end="2026-01-31"
        )
    assert job.id == 999
    assert job.status == "pending"
    assert job.file_url is None


# ═══════════════════════════════════════════════════════════════════════════
# B.13 — `GeneralLedgerExportResource` (grand livre standard)
# Pattern identique B.6 FEC, payload POST 2 champs (period_start + period_end).
# ═══════════════════════════════════════════════════════════════════════════


def _gl_pending(jid: int = 224) -> dict:
    return {
        "id": jid,
        "status": "pending",
        "created_at": "2026-05-12T11:00:00Z",
        "updated_at": "2026-05-12T11:00:00Z",
    }


def _gl_ready(jid: int = 224, file_url: str = "https://files.pennylane/gl.csv") -> dict:
    return {
        "id": jid,
        "status": "ready",
        "created_at": "2026-05-12T11:00:00Z",
        "updated_at": "2026-05-12T11:00:05Z",
        "file_url": file_url,
    }


def _gl_error(jid: int = 224) -> dict:
    return {
        "id": jid,
        "status": "error",
        "created_at": "2026-05-12T11:00:00Z",
        "updated_at": "2026-05-12T11:00:05Z",
        "file_url": None,
    }


@respx.mock
async def test_gl_create_post_returns_pending_job(settings: Settings) -> None:
    """POST OK → GeneralLedgerExportJob avec status='pending'."""
    route = respx.post(f"{_BASE}/exports/general_ledgers").respond(201, json=_gl_pending(224))
    async with PennylaneClient(settings=settings) as client:
        job = await GeneralLedgerExportResource(client).create(
            period_start="2026-01-01", period_end="2026-01-31"
        )
    assert isinstance(job, GeneralLedgerExportJob)
    assert job.id == 224
    assert job.status == "pending"
    assert job.file_url is None
    assert route.called
    sent = route.calls.last.request
    import json as _json

    body = _json.loads(sent.content)
    assert body == {"period_start": "2026-01-01", "period_end": "2026-01-31"}


@respx.mock
async def test_gl_status_get_returns_updated_job(settings: Settings) -> None:
    respx.get(f"{_BASE}/exports/general_ledgers/224").respond(200, json=_gl_ready(224))
    async with PennylaneClient(settings=settings) as client:
        job = await GeneralLedgerExportResource(client).status(224)
    assert job.id == 224
    assert job.status == "ready"
    assert job.file_url == "https://files.pennylane/gl.csv"


@respx.mock
async def test_gl_wait_polling_pending_then_ready(settings: Settings) -> None:
    """3 GET successifs : pending → pending → ready."""
    respx.get(f"{_BASE}/exports/general_ledgers/224").mock(
        side_effect=[
            httpx.Response(200, json=_gl_pending(224)),
            httpx.Response(200, json=_gl_pending(224)),
            httpx.Response(200, json=_gl_ready(224)),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        job = await GeneralLedgerExportResource(client).wait(224, poll_interval=0.01, timeout=10.0)
    assert job.status == "ready"


@respx.mock
async def test_gl_wait_polling_timeout(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pending indéfini → TimeoutError quand deadline dépassée."""
    respx.get(f"{_BASE}/exports/general_ledgers/224").respond(200, json=_gl_pending(224))
    counter = {"t": 0.0}

    def _fake_monotonic() -> float:
        counter["t"] += 0.6
        return counter["t"]

    monkeypatch.setattr(
        "grandlivre.resources.exports.time.monotonic",
        _fake_monotonic,
    )
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(TimeoutError) as excinfo:
            await GeneralLedgerExportResource(client).wait(224, poll_interval=0.01, timeout=1.0)
    assert "224" in str(excinfo.value)
    assert "pending" in str(excinfo.value)


@respx.mock
async def test_gl_wait_polling_error_raises_failed(settings: Settings) -> None:
    """status='error' → FECExportFailedError immédiate (classe générique)."""
    respx.get(f"{_BASE}/exports/general_ledgers/224").respond(200, json=_gl_error(224))
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(FECExportFailedError) as excinfo:
            await GeneralLedgerExportResource(client).wait(224, poll_interval=0.01, timeout=10.0)
    assert "224" in str(excinfo.value)
    assert "error" in str(excinfo.value)


@respx.mock
async def test_gl_download_returns_file_bytes(settings: Settings) -> None:
    gl_content = b"compte;libelle;debit;credit\n401000;Fournisseur;0.00;120.50\n"
    file_url = "https://files.pennylane/gl_export.csv"
    respx.get(f"{_BASE}/exports/general_ledgers/224").respond(
        200, json=_gl_ready(224, file_url=file_url)
    )
    respx.get(file_url).respond(200, content=gl_content)
    async with PennylaneClient(settings=settings) as client:
        payload = await GeneralLedgerExportResource(client).download(224)
    assert payload == gl_content


@respx.mock
async def test_gl_download_raises_when_file_url_missing(settings: Settings) -> None:
    respx.get(f"{_BASE}/exports/general_ledgers/224").respond(200, json=_gl_pending(224))
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(FECExportFailedError) as excinfo:
            await GeneralLedgerExportResource(client).download(224)
    assert "file_url" in str(excinfo.value)


@respx.mock
async def test_gl_create_propagates_403_when_scope_missing(
    settings: Settings,
) -> None:
    """Corollaire B.6 : scope `ledger` probablement requis aussi pour GL."""
    respx.post(f"{_BASE}/exports/general_ledgers").respond(
        403,
        json={
            "status": 403,
            "error": 'Access to this resource requires scope "ledger".',
        },
    )
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError) as excinfo:
            await GeneralLedgerExportResource(client).create(
                period_start="2026-01-01", period_end="2026-01-31"
            )
    assert excinfo.value.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# B.13 — `AnalyticalGeneralLedgerExportResource` (grand livre analytique)
# Pattern identique B.6 FEC, payload POST 3 champs (+ `mode`).
# ═══════════════════════════════════════════════════════════════════════════


def _agl_pending(jid: int = 324) -> dict:
    return {
        "id": jid,
        "status": "pending",
        "created_at": "2026-05-12T12:00:00Z",
        "updated_at": "2026-05-12T12:00:00Z",
    }


def _agl_ready(jid: int = 324, file_url: str = "https://files.pennylane/agl.csv") -> dict:
    return {
        "id": jid,
        "status": "ready",
        "created_at": "2026-05-12T12:00:00Z",
        "updated_at": "2026-05-12T12:00:05Z",
        "file_url": file_url,
    }


def _agl_error(jid: int = 324) -> dict:
    return {
        "id": jid,
        "status": "error",
        "created_at": "2026-05-12T12:00:00Z",
        "updated_at": "2026-05-12T12:00:05Z",
        "file_url": None,
    }


@respx.mock
async def test_agl_create_post_includes_mode_in_body(settings: Settings) -> None:
    """POST OK → AnalyticalGeneralLedgerExportJob ; body inclut `mode`."""
    route = respx.post(f"{_BASE}/exports/analytical_general_ledgers").respond(
        201, json=_agl_pending(324)
    )
    async with PennylaneClient(settings=settings) as client:
        job = await AnalyticalGeneralLedgerExportResource(client).create(
            period_start="2026-01-01",
            period_end="2026-01-31",
            mode="cost_center",
        )
    assert isinstance(job, AnalyticalGeneralLedgerExportJob)
    assert job.id == 324
    assert job.status == "pending"
    assert route.called
    sent = route.calls.last.request
    import json as _json

    body = _json.loads(sent.content)
    assert body == {
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "mode": "cost_center",
    }


@respx.mock
async def test_agl_status_get_returns_updated_job(settings: Settings) -> None:
    respx.get(f"{_BASE}/exports/analytical_general_ledgers/324").respond(200, json=_agl_ready(324))
    async with PennylaneClient(settings=settings) as client:
        job = await AnalyticalGeneralLedgerExportResource(client).status(324)
    assert job.id == 324
    assert job.status == "ready"
    assert job.file_url == "https://files.pennylane/agl.csv"


@respx.mock
async def test_agl_wait_polling_pending_then_ready(settings: Settings) -> None:
    respx.get(f"{_BASE}/exports/analytical_general_ledgers/324").mock(
        side_effect=[
            httpx.Response(200, json=_agl_pending(324)),
            httpx.Response(200, json=_agl_ready(324)),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        job = await AnalyticalGeneralLedgerExportResource(client).wait(
            324, poll_interval=0.01, timeout=10.0
        )
    assert job.status == "ready"


@respx.mock
async def test_agl_wait_polling_error_raises_failed(settings: Settings) -> None:
    respx.get(f"{_BASE}/exports/analytical_general_ledgers/324").respond(200, json=_agl_error(324))
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(FECExportFailedError) as excinfo:
            await AnalyticalGeneralLedgerExportResource(client).wait(
                324, poll_interval=0.01, timeout=10.0
            )
    assert "324" in str(excinfo.value)
    assert "analytique" in str(excinfo.value)


@respx.mock
async def test_agl_download_returns_file_bytes(settings: Settings) -> None:
    agl_content = b"axe;compte;libelle;debit;credit\nMARKETING;606000;Fournitures;42.00;0.00\n"
    file_url = "https://files.pennylane/agl_export.csv"
    respx.get(f"{_BASE}/exports/analytical_general_ledgers/324").respond(
        200, json=_agl_ready(324, file_url=file_url)
    )
    respx.get(file_url).respond(200, content=agl_content)
    async with PennylaneClient(settings=settings) as client:
        payload = await AnalyticalGeneralLedgerExportResource(client).download(324)
    assert payload == agl_content


@respx.mock
async def test_agl_download_raises_when_file_url_missing(settings: Settings) -> None:
    respx.get(f"{_BASE}/exports/analytical_general_ledgers/324").respond(
        200, json=_agl_pending(324)
    )
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(FECExportFailedError) as excinfo:
            await AnalyticalGeneralLedgerExportResource(client).download(324)
    assert "file_url" in str(excinfo.value)


@respx.mock
async def test_agl_create_propagates_403_when_scope_missing(
    settings: Settings,
) -> None:
    """Corollaire B.6 : scope `ledger` probablement requis aussi pour AGL."""
    respx.post(f"{_BASE}/exports/analytical_general_ledgers").respond(
        403,
        json={
            "status": 403,
            "error": 'Access to this resource requires scope "ledger".',
        },
    )
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError) as excinfo:
            await AnalyticalGeneralLedgerExportResource(client).create(
                period_start="2026-01-01",
                period_end="2026-01-31",
                mode="cost_center",
            )
    assert excinfo.value.status_code == 403
