"""Tests méchants de LedgerEntriesResource.create() / recover() (ADR revue v4).

Règle v4 (b) ciblée — l'invariant prouvé ici :
  * SEUL un **201 propre** confirme (accusé de réception autoritative de l'unique POST) ;
  * le **read-back ne confirme JAMAIS** : tout chemin incertain (timeout/5xx/illisible) →
    `WALInDoubtError`, candidats empreinte-identique présentés à l'humain (jamais d'auto-
    confirm silencieux, jamais de re-POST) ;
  * `currency` est TOUJOURS envoyée (multi-devises) + DANS le fingerprint (dedup €≠$) mais
    PAS dans la clé de read-back (non ré-observable, spec OpenAPI) ; `piece_number` jamais
    envoyé (auto Pennylane).

Fixtures GROUNDÉES sur la VRAIE forme API (instruction #0, spec + sandbox read-only) :
LISTE sans `ledger_entry_lines` (RB-1) ; DÉTAIL avec lignes, `ledger_account` imbriqué (RB-3),
`label`/`due_date` entrée + `label` ligne (ré-observables, dans la clé de read-back).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from grandlivre import (
    CreatedLedgerEntry,
    NewLedgerEntryLine,
    PennylaneValidationError,
    UnbalancedEntryError,
    WALInDoubtError,
    WALStoreRequiredError,
)
from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.resources.ledger_entries import LedgerEntriesResource
from grandlivre.wal import (
    JsonlStore,
    NullStore,
    SqliteIdempotencyStore,
    WALEntry,
    WALState,
    WALStoreError,
)

_BASE = "https://app.pennylane.com/api/external/v2"
_NS = "dossier-test"


def _balanced_lines() -> list[NewLedgerEntryLine]:
    # Libellés de ligne ALIGNÉS sur `_detail` (la signature read-back v4 inclut le libellé).
    return [
        NewLedgerEntryLine(ledger_account_id=486000, debit=Decimal("100.00"), label="CCA"),
        NewLedgerEntryLine(ledger_account_id=606300, credit=Decimal("100.00")),
    ]


def _list_item(entry_id: int = 12345, label: str = "OD CCA", due_date: str | None = None) -> dict:
    """Forme RÉELLE LISTE (spec + sandbox) : AUCUN `ledger_entry_lines` ; expose
    `id`/`date`/`label`/`due_date`/`journal_id` (ré-observables, pré-filtre read-back)."""
    return {
        "id": entry_id,
        "label": label,
        "date": "2026-03-31",
        "journal_id": 42,
        "journal": {"id": 42},
        "piece_number": "AUTO-PNL",
        "due_date": due_date,
        "invoice_number": "",
        "attachment": None,
        "categories": [],
        "ledger_attachment_filename": None,
        "status": "validated",
        "created_at": "2026-03-31T10:00:00+00:00",
        "updated_at": "2026-03-31T10:00:00+00:00",
    }


def _detail(entry_id: int = 12345, label: str = "OD CCA", due_date: str | None = None) -> dict:
    """Forme RÉELLE DÉTAIL (clés exactes du GET réel — alignée campagne C1 2026-06-10) :
    lignes avec `ledger_account` IMBRIQUÉ + `label` ligne ; `journal` OBJET — PAS de
    `journal_id` top-level (ABSENT du DÉTAIL réel, contrairement à la LISTE qui l'expose) ;
    PAS de `currency` (jamais renvoyée — même pour une écriture créée en devise, les
    montants des lignes reviennent CONVERTIS en devise de tenue, grounding W8)."""
    return {
        "id": entry_id,
        "label": label,
        "date": "2026-03-31",
        "journal": {"id": 42},
        "due_date": due_date,
        "piece_number": "AUTO-PNL",
        "invoice_number": "",
        "status": "validated",
        "created_at": "2026-03-31T10:00:00.000000Z",
        "updated_at": "2026-03-31T10:00:00.000000Z",
        "attachment": None,
        "categories": [],
        "ledger_entry_lines": [
            {
                "id": 9001,
                "debit": "100.00",
                "credit": "0.00",
                "label": "CCA",
                "ledger_account": {"id": 486000, "number": "486000", "url": "x"},
            },
            {
                "id": 9002,
                "debit": "0.00",
                "credit": "100.00",
                "label": None,
                "ledger_account": {"id": 606300, "number": "606300", "url": "x"},
            },
        ],
    }


def _mock_ledger_get(
    *, watermark: int = 0, list_items: list[dict] | None = None, list_status: int = 200
):
    """Mock unique du GET `/ledger_entries` : branche watermark (`sort=-id` → id_max) vs
    read-back (filtre → liste). `watermark=0` = ledger vide."""
    items = list_items if list_items is not None else []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("sort") == "-id":
            wm = [{"id": watermark}] if watermark else []
            return httpx.Response(200, json={"items": wm})
        if list_status != 200:
            return httpx.Response(list_status, json={"error": "x", "status": list_status})
        return httpx.Response(200, json={"items": items})

    return respx.get(f"{_BASE}/ledger_entries").mock(side_effect=_handler)


def _resource(client: PennylaneClient, store: JsonlStore) -> LedgerEntriesResource:
    return LedgerEntriesResource(client, wal_store=store)


def _kwargs(**over: object) -> dict:
    base = {
        "date": "2026-03-31",
        "label": "OD CCA",
        "journal_id": 42,
        "lines": _balanced_lines(),
        "namespace": _NS,
    }
    base.update(over)
    return base


# ─── Sentinelles forme réelle (RB-1 / RB-3) ───────────────────────────────────


def test_list_item_has_no_lines() -> None:
    assert CreatedLedgerEntry.model_validate(_list_item()).ledger_entry_lines == []


def test_detail_line_nested_account_resolved() -> None:
    line = CreatedLedgerEntry.model_validate(_detail()).ledger_entry_lines[0]
    assert line.ledger_account_id is None
    assert line.resolved_account_id == 486000


# ─── Happy path + balance gate + 422 ──────────────────────────────────────────


@respx.mock
async def test_create_happy_201_confirmed(settings: Settings, tmp_path: Path) -> None:
    # SEUL le 201 propre confirme.
    _mock_ledger_get(watermark=0)
    post = respx.post(f"{_BASE}/ledger_entries").respond(201, json=_detail())
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        created = await _resource(client, store).create(**_kwargs())
    assert created.id == 12345
    assert post.call_count == 1


@respx.mock
async def test_create_unbalanced_no_post(settings: Settings, tmp_path: Path) -> None:
    post = respx.post(f"{_BASE}/ledger_entries").respond(201, json=_detail())
    store = JsonlStore(tmp_path / "wal.jsonl")
    bad = [
        NewLedgerEntryLine(ledger_account_id=486000, debit=Decimal("100.00")),
        NewLedgerEntryLine(ledger_account_id=606300, credit=Decimal("90.00")),
    ]
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(UnbalancedEntryError):
            await _resource(client, store).create(
                date="2026-03-31", label="OD", journal_id=42, lines=bad, namespace=_NS
            )
    assert post.call_count == 0


@respx.mock
async def test_create_422_failed_no_retry(settings: Settings, tmp_path: Path) -> None:
    _mock_ledger_get(watermark=0)
    post = respx.post(f"{_BASE}/ledger_entries").respond(
        422, json={"error": "journal fermé", "status": 422}
    )
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneValidationError):
            await _resource(client, store).create(**_kwargs())
    assert post.call_count == 1


# ─── Cœur v4 : le read-back ne confirme JAMAIS → IN_DOUBT ──────────────────────


@respx.mock
async def test_create_timeout_readback_match_in_doubt_never_confirm(
    settings: Settings, tmp_path: Path
) -> None:
    # POST timeout → read-back trouve 1 candidat empreinte-identique (id 777) → IN_DOUBT
    # (JAMAIS confirm, règle (b) ciblée). Le candidat est PRÉSENTÉ, pas confirmé.
    _mock_ledger_get(watermark=0, list_items=[_list_item(777)])
    post = respx.post(f"{_BASE}/ledger_entries").mock(side_effect=httpx.ReadTimeout("t"))
    respx.get(f"{_BASE}/ledger_entries/777").respond(200, json=_detail(777))
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(WALInDoubtError) as ei:
            await _resource(client, store).create(**_kwargs())
    assert ei.value.candidates == [777]
    assert post.call_count == 1  # un seul POST, aucun re-POST
    assert store.get(_NS, ei.value.fingerprint).state is WALState.IN_DOUBT  # type: ignore[union-attr]


@respx.mock
async def test_create_timeout_zero_match_in_doubt_no_repost(
    settings: Settings, tmp_path: Path
) -> None:
    # POST timeout → 0 candidat visible → IN_DOUBT (jamais re-POST : lag read-your-writes,
    # l'écriture a peut-être atterri sans être visible). NB3-2 fermé.
    _mock_ledger_get(watermark=0, list_items=[])
    post = respx.post(f"{_BASE}/ledger_entries").mock(side_effect=httpx.ReadTimeout("t"))
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(WALInDoubtError) as ei:
            await _resource(client, store).create(**_kwargs())
    assert ei.value.candidates == []
    assert post.call_count == 1  # AUCUN re-POST


@respx.mock
async def test_create_5xx_in_doubt(settings: Settings, tmp_path: Path) -> None:
    _mock_ledger_get(watermark=0, list_items=[])
    post = respx.post(f"{_BASE}/ledger_entries").respond(503)
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(WALInDoubtError):
            await _resource(client, store).create(**_kwargs())
    assert post.call_count == 1  # un seul POST (pas de boucle re-POST)


@respx.mock
async def test_create_sentinelle_lag_tardif_homonyme_in_doubt(
    settings: Settings, tmp_path: Path
) -> None:
    # SENTINELLE §1 (lag-tardif ∩ homonyme empreinte-identique) : POST incertain + read-back
    # voit 1 candidat empreinte-identique d'id>watermark → IN_DOUBT (jamais confirm). Verrouille
    # la régression : on ne confirme JAMAIS sur un id read-back même empreinte-identique.
    _mock_ledger_get(watermark=500, list_items=[_list_item(900)])
    post = respx.post(f"{_BASE}/ledger_entries").mock(side_effect=httpx.ReadTimeout("t"))
    respx.get(f"{_BASE}/ledger_entries/900").respond(200, json=_detail(900))
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(WALInDoubtError) as ei:
            await _resource(client, store).create(**_kwargs())
    assert ei.value.candidates == [900]  # présenté à l'humain, JAMAIS confirmé
    assert post.call_count == 1  # un seul POST, aucun re-POST


# ─── Match read-back ré-observable : label / due_date discriminent ─────────────


@respx.mock
async def test_readback_excludes_preexisting_by_watermark(
    settings: Settings, tmp_path: Path
) -> None:
    # Un candidat d'id <= watermark (créé AVANT notre repère) n'est pas notre écriture →
    # exclu → 0 candidat → IN_DOUBT (détail jamais fetché).
    _mock_ledger_get(watermark=500, list_items=[_list_item(400)])
    respx.post(f"{_BASE}/ledger_entries").mock(side_effect=httpx.ReadTimeout("t"))
    # Détail de 400 NON mocké : exclu avant le GET → respx lèverait si demandé.
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(WALInDoubtError) as ei:
            await _resource(client, store).create(**_kwargs())
    assert ei.value.candidates == []


@respx.mock
async def test_readback_distinct_label_not_surfaced(settings: Settings, tmp_path: Path) -> None:
    # Un candidat de MÊME signature lignes mais label entrée DIFFÉRENT (fingerprint distinct)
    # n'est pas empreinte-identique → pré-filtre LISTE l'écarte → 0 candidat → IN_DOUBT.
    _mock_ledger_get(watermark=0, list_items=[_list_item(800, label="AUTRE LABEL")])
    respx.post(f"{_BASE}/ledger_entries").mock(side_effect=httpx.ReadTimeout("t"))
    # Détail de 800 NON mocké : écarté au pré-filtre (label) avant le GET.
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(WALInDoubtError) as ei:
            await _resource(client, store).create(**_kwargs())
    assert ei.value.candidates == []


# ─── Currency : sentinelle multi-devises (C re-revue (a)(b)(c)) ────────────────


@respx.mock
async def test_currency_distinct_fingerprints_both_written(
    settings: Settings, tmp_path: Path
) -> None:
    # 2 écritures MÊMES LIGNES, devises DIFFÉRENTES → fingerprints DISTINCTS (currency dans
    # le fingerprint) → toutes deux écrites (201 chacune, pas de faux DUPLICATE). currency
    # TOUJOURS envoyée (présente dans le payload POST).
    _mock_ledger_get(watermark=0)
    posted_bodies: list[dict] = []

    def _post_handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        posted_bodies.append(_json.loads(request.content))
        return httpx.Response(201, json=_detail(1000 + len(posted_bodies)))

    respx.post(f"{_BASE}/ledger_entries").mock(side_effect=_post_handler)
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        res = _resource(client, store)
        e_eur = await res.create(**_kwargs(currency="EUR"))
        e_usd = await res.create(**_kwargs(currency="USD"))
    assert e_eur.id != e_usd.id  # 2 écritures distinctes (pas dédupliquées)
    assert len(posted_bodies) == 2
    # currency TOUJOURS envoyée au write.
    assert posted_bodies[0]["currency"] == "EUR"
    assert posted_bodies[1]["currency"] == "USD"


@respx.mock
async def test_currency_in_fingerprint_not_in_readback_key(
    settings: Settings, tmp_path: Path
) -> None:
    # currency est dans le fingerprint (dedup €≠$, sentinelle ci-dessus) mais JAMAIS dans la
    # clé de read-back : l'API ne renvoie pas currency (spec OpenAPI) → non ré-observable.
    # L'inclure casserait toute réconciliation (aucun candidat ne matcherait). Verrou anti-régression.
    _mock_ledger_get(watermark=0, list_items=[])
    respx.post(f"{_BASE}/ledger_entries").mock(side_effect=httpx.ReadTimeout("t"))
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(WALInDoubtError) as ei:
            await _resource(client, store).create(**_kwargs(currency="USD"))
    entry = store.get(_NS, ei.value.fingerprint)
    assert entry is not None and entry.readback_key is not None
    rb = entry.readback_key
    assert "currency" not in rb
    # Sous-ensemble ré-observable strict : aucune ligne de signature ne porte la devise.
    assert all("USD" not in str(part) for sig in rb["line_signature"] for part in sig)


# ─── Taxonomie : 201 illisible / sans id → IN_DOUBT ───────────────────────────


@respx.mock
async def test_create_201_unreadable_body_in_doubt(settings: Settings, tmp_path: Path) -> None:
    _mock_ledger_get(watermark=0, list_items=[_list_item(555)])
    post = respx.post(f"{_BASE}/ledger_entries").respond(201, text="<html>oops</html>")
    respx.get(f"{_BASE}/ledger_entries/555").respond(200, json=_detail(555))
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(WALInDoubtError) as ei:
            await _resource(client, store).create(**_kwargs())
    assert ei.value.candidates == [555]
    assert post.call_count == 1


@respx.mock
async def test_create_readback_unavailable_in_doubt_not_orphan(
    settings: Settings, tmp_path: Path
) -> None:
    # POST timeout puis read-back LISTE 403 → IN_DOUBT (candidats inconnus), jamais orphelin.
    _mock_ledger_get(watermark=0, list_status=403)
    post = respx.post(f"{_BASE}/ledger_entries").mock(side_effect=httpx.ReadTimeout("t"))
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(WALInDoubtError) as ei:
            await _resource(client, store).create(**_kwargs())
    entry = store.get(_NS, ei.value.fingerprint)
    assert entry is not None and entry.state is WALState.IN_DOUBT
    assert ei.value.candidates == []
    assert post.call_count == 1


# ─── MF5 : ConnectError (jamais envoyé) vs ConnectTimeout (peut-être envoyé) ───


@respx.mock
async def test_mf5_connect_error_failed_no_readback(settings: Settings, tmp_path: Path) -> None:
    # ConnectError = connexion jamais établie → octets jamais envoyés → FAILED. Aucun
    # read-back (le GET read-back n'est PAS mocké → respx lèverait si appelé).
    respx.get(f"{_BASE}/ledger_entries", params={"sort": "-id"}).respond(200, json={"items": []})
    post = respx.post(f"{_BASE}/ledger_entries").mock(side_effect=httpx.ConnectError("refused"))
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(httpx.ConnectError):
            await _resource(client, store).create(**_kwargs())
    assert post.call_count >= 1


@respx.mock
async def test_mf5_connect_timeout_in_doubt(settings: Settings, tmp_path: Path) -> None:
    # ConnectTimeout = « peut-être envoyé » → read-back → IN_DOUBT (jamais confirm).
    _mock_ledger_get(watermark=0, list_items=[_list_item(700)])
    post = respx.post(f"{_BASE}/ledger_entries").mock(
        side_effect=httpx.ConnectTimeout("connect timeout")
    )
    respx.get(f"{_BASE}/ledger_entries/700").respond(200, json=_detail(700))
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(WALInDoubtError) as ei:
            await _resource(client, store).create(**_kwargs())
    assert ei.value.candidates == [700]
    assert post.call_count == 1


# ─── STORE-1 : write sans store WAL réel refusé (NullStore interdit en écriture) ──


@respx.mock
async def test_store1_nullstore_write_rejected_no_post(settings: Settings, tmp_path: Path) -> None:
    # STORE-1 (ADR) : un create() (write) sans store WAL réel (NullStore ou
    # absent) est REFUSÉ fail-fast AVANT tout effet — pas même le GET watermark (non mocké :
    # respx lèverait s'il était appelé), aucun POST. Garde non-omissible AVANT le write live.
    post = respx.post(f"{_BASE}/ledger_entries").respond(201, json=_detail())
    async with PennylaneClient(settings=settings) as client:
        # wal_store absent → NullStore par défaut → write interdit.
        with pytest.raises(WALStoreRequiredError):
            await LedgerEntriesResource(client).create(**_kwargs())
        # NullStore explicite → idem.
        with pytest.raises(WALStoreRequiredError):
            await LedgerEntriesResource(client, wal_store=NullStore()).create(**_kwargs())
    assert post.call_count == 0  # fail-fast : jamais de POST


# ─── Watermark fail-closed ─────────────────────────────────────────────────────


@respx.mock
async def test_watermark_fetch_failure_no_post(settings: Settings, tmp_path: Path) -> None:
    # Le GET sort=-id (watermark) échoue (403) AVANT le POST → exception propagée, AUCUN POST
    # (fail-closed : pas d'écriture sans repère). Le POST n'est PAS mocké → respx lèverait.
    respx.get(f"{_BASE}/ledger_entries", params={"sort": "-id"}).respond(
        403, json={"error": "forbidden", "status": 403}
    )
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(Exception):  # noqa: B017 (PennylaneAuthError, propagée)
            await _resource(client, store).create(**_kwargs())
    # Aucune entrée WAL créée (pas de réservation avant le watermark).
    assert store.iter_nonterminal() == []


# ─── Idempotence + contention ─────────────────────────────────────────────────


@respx.mock
async def test_create_replay_short_circuit_zero_post(settings: Settings, tmp_path: Path) -> None:
    _mock_ledger_get(watermark=0)
    post = respx.post(f"{_BASE}/ledger_entries").respond(201, json=_detail(12345))
    # Edge delete↔WAL : le court-circuit VÉRIFIE l'existence de l'id mémorisé (GET détail).
    detail = respx.get(f"{_BASE}/ledger_entries/12345").respond(200, json=_detail(12345))
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        first = await _resource(client, store).create(**_kwargs())
        second = await _resource(client, store).create(**_kwargs())
    assert first.id == 12345 and second.id == 12345
    assert post.call_count == 1  # replay → short-circuit, zéro re-POST
    assert detail.call_count == 1  # 1 vérification d'existence (le seul replay)


@respx.mock
async def test_create_contention_single_post(settings: Settings, tmp_path: Path) -> None:
    _mock_ledger_get(watermark=0)
    post = respx.post(f"{_BASE}/ledger_entries").respond(201, json=_detail(12345))
    # Les 7 perdants de la contention court-circuitent → vérification d'existence chacun.
    respx.get(f"{_BASE}/ledger_entries/12345").respond(200, json=_detail(12345))
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        res = _resource(client, store)
        results = await asyncio.gather(*[res.create(**_kwargs()) for _ in range(8)])
    assert post.call_count == 1
    assert all(r.id == 12345 for r in results)


# ─── Edge delete↔WAL (campagne 2026-06-10) : id confirmé supprimé via l'UI ─────


def _age_wal_entry(db_path: Path, *, seconds: int = 3600) -> None:
    """Vieillit ``updated_at`` (garde d'âge RES-1) directement en base SQLite — le store
    fige now() à chaque transition, le test simule un mapping LONGUEMENT visible."""
    import sqlite3
    from datetime import UTC as _UTC
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    old = (_dt.now(tz=_UTC) - _td(seconds=seconds)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE wal_entries SET updated_at = ?", (old,))
        conn.commit()


@respx.mock
async def test_short_circuit_old_dead_id_invalidates_and_rewrites(
    settings: Settings, tmp_path: Path
) -> None:
    # Mapping CONFIRMED ANCIEN (vieilli > garde d'âge) → 777 ; l'écriture a été SUPPRIMÉE
    # via l'UI Pennylane (l'API V2 n'a pas de DELETE — spec + sonde C3). Re-soumission →
    # suppression conclue sur **2× 404 espacés de la grâce DÉDIÉE** (D1/RES-1) →
    # invalidation conditionnelle par IDENTITÉ (D2) → RÉ-ÉCRITURE (2e POST → 888).
    # L'appelant ne reçoit JAMAIS l'id mort.
    _mock_ledger_get(watermark=0)
    post = respx.post(f"{_BASE}/ledger_entries").mock(
        side_effect=[
            httpx.Response(201, json=_detail(777)),
            httpx.Response(201, json=_detail(888)),
        ]
    )
    detail = respx.get(f"{_BASE}/ledger_entries/777").respond(
        404, json={"status": 404, "error": "Couldn't find LedgerEntry with 'id'=777"}
    )
    store = SqliteIdempotencyStore(tmp_path / "wal.db")
    async with PennylaneClient(settings=settings) as client:
        res = _resource(client, store)
        first = await res.create(**_kwargs())
        assert first.id == 777
        _age_wal_entry(tmp_path / "wal.db")  # mapping ANCIEN → chemin heal
        second = await res.create(**_kwargs())  # 777 supprimée entre-temps
    assert second.id == 888  # ré-écriture — jamais l'id mort
    assert post.call_count == 2
    assert detail.call_count == 2  # D1 : DEUX sondes 404 espacées avant de conclure
    assert store.find_confirmed_fingerprint(_NS, 888) is not None  # nouveau mapping
    assert store.find_confirmed_fingerprint(_NS, 777) is None  # ancien mapping invalidé


@respx.mock
async def test_young_dead_id_goes_in_doubt_guarded_then_resolve_rewrites(
    settings: Settings, tmp_path: Path
) -> None:
    # RES-1 garde d'âge : double-404 sur une écriture confirmée RÉCEMMENT (< garde) →
    # PROBABLEMENT vivante (lag read-after-write long) → bascule GARDÉE CONFIRMED→IN_DOUBT
    # + WALInDoubtError (humain) — JAMAIS d'invalidation + ré-écriture automatiques.
    # Cercle complet : resolve(landed=False) → FAILED → la re-soumission RÉ-ÉCRIT.
    _mock_ledger_get(watermark=0)
    post = respx.post(f"{_BASE}/ledger_entries").mock(
        side_effect=[
            httpx.Response(201, json=_detail(777)),
            httpx.Response(201, json=_detail(888)),
        ]
    )
    respx.get(f"{_BASE}/ledger_entries/777").respond(
        404, json={"status": 404, "error": "Couldn't find LedgerEntry with 'id'=777"}
    )
    store = SqliteIdempotencyStore(tmp_path / "wal.db")
    async with PennylaneClient(settings=settings) as client:
        res = _resource(client, store)
        first = await res.create(**_kwargs())
        assert first.id == 777
        with pytest.raises(WALInDoubtError) as ei:  # JEUNE (pas de vieillissement)
            await res.create(**_kwargs())
        assert ei.value.candidates == [777]  # le dead_id est surfacé à l'humain
        doubted = store.get(_NS, ei.value.fingerprint)
        assert doubted is not None
        assert doubted.state is WALState.IN_DOUBT  # bascule GARDÉE (resolve utilisable)
        assert doubted.pennylane_id is None
        assert post.call_count == 1  # ZÉRO ré-écriture automatique sur écriture jeune
        # Résolution humaine « non, elle n'existe plus » → ré-écriture autorisée.
        await res._get_wal().resolve(namespace=_NS, fingerprint=ei.value.fingerprint, landed=False)
        healed = await res.create(**_kwargs())
    assert healed.id == 888
    assert post.call_count == 2  # la ré-écriture n'arrive QUE post-résolution humaine


@respx.mock
async def test_short_circuit_transient_404_is_conservative(
    settings: Settings, tmp_path: Path
) -> None:
    # D1 (revue (c) F1) : un 404 TRANSITOIRE (lag read-after-write) sur le chemin NOMINAL
    # (re-soumission idempotente rapide post-201) ne doit JAMAIS invalider ni ré-écrire —
    # la 2e sonde (espacée du délai de grâce) voit l'écriture VIVANTE → id mémorisé renvoyé.
    _mock_ledger_get(watermark=0)
    post = respx.post(f"{_BASE}/ledger_entries").respond(201, json=_detail(777))
    detail = respx.get(f"{_BASE}/ledger_entries/777").mock(
        side_effect=[
            httpx.Response(404, json={"status": 404, "error": "Couldn't find LedgerEntry"}),
            httpx.Response(200, json=_detail(777)),
        ]
    )
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        res = _resource(client, store)
        first = await res.create(**_kwargs())
        second = await res.create(**_kwargs())  # replay rapide : 404 transitoire au 1er GET
    assert first.id == 777 and second.id == 777  # id mémorisé — zéro invalidation
    assert post.call_count == 1  # ZÉRO ré-écriture : pas de doublon silencieux
    assert detail.call_count == 2  # 404 puis 200 (lag confirmé transitoire)
    assert store.find_confirmed_fingerprint(_NS, 777) is not None  # mapping intact


@respx.mock
async def test_dead_id_race_loser_returns_fresh_mapping_zero_double_post(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # D2 (revue (c) F2) : course P1/P2 — P2 tient un dead-id PÉRIMÉ (777, mapping ANCIEN) ;
    # juste avant son invalidation, P1 a re-confirmé le mapping sur l'id VIVANT 888.
    # L'invalidation conditionnelle par identité REFUSE (False, atomique store-level) → P2
    # rejoue sa réservation, court-circuite sur le mapping FRAIS, vérifie 888 vivant →
    # renvoie 888. AUCUN 2e POST : zéro doublon.
    from grandlivre.wal import WALManager

    _mock_ledger_get(watermark=0)
    post = respx.post(f"{_BASE}/ledger_entries").respond(201, json=_detail(777))
    respx.get(f"{_BASE}/ledger_entries/777").respond(
        404, json={"status": 404, "error": "Couldn't find LedgerEntry with 'id'=777"}
    )
    respx.get(f"{_BASE}/ledger_entries/888").respond(200, json=_detail(888))
    store = SqliteIdempotencyStore(tmp_path / "wal.db")
    real_invalidate = WALManager.invalidate_confirmed

    async def race_then_invalidate(
        self: WALManager, ns: str, fp: str, *, expected_pennylane_id: int, reason: str
    ) -> bool:
        # P1 gagne la course : le mapping est re-confirmé sur l'id VIVANT 888 juste
        # avant que P2 (nous) tente d'invalider avec son dead-id périmé 777.
        store.finalize(ns, fp, WALState.CONFIRMED, 888, None)
        return await real_invalidate(
            self, ns, fp, expected_pennylane_id=expected_pennylane_id, reason=reason
        )

    async with PennylaneClient(settings=settings) as client:
        res = _resource(client, store)
        first = await res.create(**_kwargs())
        assert first.id == 777
        _age_wal_entry(tmp_path / "wal.db")  # ANCIEN → chemin invalidation (pas la garde d'âge)
        monkeypatch.setattr(WALManager, "invalidate_confirmed", race_then_invalidate)
        second = await res.create(**_kwargs())
    assert second.id == 888  # le perdant renvoie le mapping FRAIS — jamais d'invalidation aveugle
    assert post.call_count == 1  # UN SEUL POST au total : zéro doublon
    confirmed = store.get(_NS, store.find_confirmed_fingerprint(_NS, 888) or "")
    assert confirmed is not None and confirmed.pennylane_id == 888  # mapping vivant intact


class _ConcurrentReconfirmStore:
    """Double de CONCURRENCE au seam store (RES-2, « test sans lying-mock ») : toutes les
    opérations sont RÉELLES (délégation) ; après chaque invalidation RÉUSSIE vers FAILED,
    un écrivain concurrent re-confirme le même fingerprint sur un AUTRE id (lui aussi
    mort) — l'entrelacement pathologique exact, exprimé à la frontière du store."""

    def __init__(self, inner: SqliteIdempotencyStore, next_dead_id: int) -> None:
        self._inner = inner
        self._next_dead_id = next_dead_id

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def transition_confirmed_if(
        self, ns: str, fp: str, expected: int, to_state: WALState, reason: str
    ) -> bool:
        ok = self._inner.transition_confirmed_if(ns, fp, expected, to_state, reason)
        if ok and to_state is WALState.FAILED:
            # L'écrivain concurrent re-confirme aussitôt sur un autre id (mort lui aussi).
            self._inner.finalize(ns, fp, WALState.CONFIRMED, self._next_dead_id, None)
        return ok


@respx.mock
async def test_dead_id_exhaustion_guarded_in_doubt_resolve_usable(
    settings: Settings, tmp_path: Path
) -> None:
    # RES-2 (revue (c)) : épuisement — chaque passe re-court-circuite sur un id mort
    # (concurrence pathologique simulée au seam STORE, opérations réelles). Issue :
    # bascule GARDÉE CONFIRMED→IN_DOUBT (resolve() utilisable) + WALInDoubtError portant
    # le DERNIER dead_id en candidat. Zéro POST sur ce chemin.
    _mock_ledger_get(watermark=0)
    post = respx.post(f"{_BASE}/ledger_entries").respond(201, json=_detail(777))
    for dead in (777, 888):
        respx.get(f"{_BASE}/ledger_entries/{dead}").respond(
            404, json={"status": 404, "error": f"Couldn't find LedgerEntry with 'id'={dead}"}
        )
    inner = SqliteIdempotencyStore(tmp_path / "wal.db")
    store = _ConcurrentReconfirmStore(inner, next_dead_id=888)
    async with PennylaneClient(settings=settings) as client:
        res = LedgerEntriesResource(client, wal_store=store)
        first = await res.create(**_kwargs())
        assert first.id == 777
        _age_wal_entry(tmp_path / "wal.db")  # ANCIEN → passe 1 = invalidation réelle
        with pytest.raises(WALInDoubtError) as ei:
            await res.create(**_kwargs())
    assert ei.value.candidates == [888]  # le DERNIER dead_id est surfacé à l'humain
    assert post.call_count == 1  # jamais de POST sur ce chemin pathologique
    final = inner.get(_NS, ei.value.fingerprint)
    assert final is not None
    assert final.state is WALState.IN_DOUBT  # bascule GARDÉE → resolve() utilisable
    assert final.pennylane_id is None


@respx.mock
async def test_probe_404_then_5xx_is_conservative(settings: Settings, tmp_path: Path) -> None:
    # RES-4 (revue (c)) : [404, 5xx] — la 2e sonde INVÉRIFIABLE n'autorise JAMAIS à
    # conclure « supprimée » → conservateur (id mémorisé, zéro invalidation, zéro POST).
    _mock_ledger_get(watermark=0)
    post = respx.post(f"{_BASE}/ledger_entries").respond(201, json=_detail(777))
    respx.get(f"{_BASE}/ledger_entries/777").mock(
        side_effect=[httpx.Response(404, json={"status": 404, "error": "x"})]
        + [httpx.Response(503)] * 6  # 2e sonde : 5xx (re-tentés par le retryer GET)
    )
    store = SqliteIdempotencyStore(tmp_path / "wal.db")
    async with PennylaneClient(settings=settings) as client:
        res = _resource(client, store)
        first = await res.create(**_kwargs())
        _age_wal_entry(tmp_path / "wal.db")  # même ANCIEN : un doute reste un doute
        second = await res.create(**_kwargs())
    assert first.id == 777 and second.id == 777  # conservateur
    assert post.call_count == 1  # zéro ré-écriture
    assert store.find_confirmed_fingerprint(_NS, 777) is not None  # mapping intact


@respx.mock
async def test_short_circuit_existence_unverifiable_is_conservative(
    settings: Settings, tmp_path: Path
) -> None:
    # Vérification d'existence INVÉRIFIABLE (5xx) → conservateur : id mémorisé renvoyé tel
    # quel (comportement antérieur), AUCUNE ré-écriture (on ne re-POSTe jamais sur un doute).
    _mock_ledger_get(watermark=0)
    post = respx.post(f"{_BASE}/ledger_entries").respond(201, json=_detail(777))
    respx.get(f"{_BASE}/ledger_entries/777").respond(503)
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        res = _resource(client, store)
        first = await res.create(**_kwargs())
        second = await res.create(**_kwargs())
    assert first.id == 777 and second.id == 777
    assert post.call_count == 1  # zéro ré-écriture
    assert store.find_confirmed_fingerprint(_NS, 777) is not None  # mapping intact


# ─── resolve() humain : IN_DOUBT → CONFIRMED → short-circuit ──────────────────


@respx.mock
async def test_resolve_in_doubt_then_short_circuit(settings: Settings, tmp_path: Path) -> None:
    _mock_ledger_get(watermark=0, list_items=[_list_item(1)])
    post = respx.post(f"{_BASE}/ledger_entries").mock(side_effect=httpx.ReadTimeout("t"))
    respx.get(f"{_BASE}/ledger_entries/1").respond(200, json=_detail(1))
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        res = _resource(client, store)
        with pytest.raises(WALInDoubtError) as ei:
            await res.create(**_kwargs())
        fingerprint = ei.value.fingerprint
        # Résolution humaine : l'expert-comptable a vérifié chez Pennylane que l'écriture #1 est la nôtre.
        await res._get_wal().resolve(
            namespace=_NS, fingerprint=fingerprint, landed=True, pennylane_id=1
        )
        resolved = await res.create(**_kwargs())
    assert resolved.id == 1
    assert post.call_count == 1  # post-resolve : short-circuit, pas de re-POST


# ─── recover() v4 : IN_FLIGHT → IN_DOUBT (jamais confirm), ré-entrant ─────────


def _crash_in_flight(store: JsonlStore, fingerprint: str, *, id_watermark: int = 0) -> None:
    """Simule un crash : entrée IN_FLIGHT durable (clé read-back + watermark persistés)."""
    store.reserve_in_flight(
        _NS,
        fingerprint,
        "key-crash",
        readback_key={
            "date": "2026-03-31",
            "journal_id": 42,
            "label": "OD CCA",
            "due_date": None,
            "line_signature": [
                [486000, "100.00", "0.00", "CCA"],
                [606300, "0.00", "100.00", ""],
            ],
        },
        id_watermark=id_watermark,
    )


@respx.mock
async def test_recover_in_flight_always_in_doubt(settings: Settings, tmp_path: Path) -> None:
    # recover() ne confirme JAMAIS (v4) : une IN_FLIGHT au crash → IN_DOUBT, candidats
    # présentés (ici l'écriture a atterri id 800, mais l'humain tranche).
    respx.get(f"{_BASE}/ledger_entries").respond(200, json={"items": [_list_item(800)]})
    respx.get(f"{_BASE}/ledger_entries/800").respond(200, json=_detail(800))
    store = JsonlStore(tmp_path / "wal.jsonl")
    _crash_in_flight(store, "fp-landed", id_watermark=0)
    async with PennylaneClient(settings=settings) as client:
        report = await _resource(client, store).recover()
    assert report[0]["action"] == "in_doubt"
    assert report[0]["candidates"] == [800]
    entry = store.get(_NS, "fp-landed")
    assert entry is not None and entry.state is WALState.IN_DOUBT


@respx.mock
async def test_recover_reentrant_and_leaves_in_doubt_untouched(
    settings: Settings, tmp_path: Path
) -> None:
    # Ré-entrant : un 2ᵉ recover() ne re-transitionne pas (l'entrée est déjà IN_DOUBT → exclue
    # du filtre IN_FLIGHT). Une entrée DÉJÀ IN_DOUBT n'est jamais auto-résolue (REC-2).
    respx.get(f"{_BASE}/ledger_entries").respond(200, json={"items": []})
    store = JsonlStore(tmp_path / "wal.jsonl")
    _crash_in_flight(store, "fp-x", id_watermark=0)
    async with PennylaneClient(settings=settings) as client:
        res = _resource(client, store)
        r1 = await res.recover()
        r2 = await res.recover()  # ré-entrant
    assert r1[0]["action"] == "in_doubt"
    assert r2 == []  # plus aucune IN_FLIGHT au 2ᵉ tour
    assert store.get(_NS, "fp-x").state is WALState.IN_DOUBT  # type: ignore[union-attr]


# ─── NBI-1 : recover() hermétique — readback_key malformé n'avorte pas la reprise ─


@respx.mock
async def test_recover_malformed_readback_key_hermetic(settings: Settings, tmp_path: Path) -> None:
    # NBI-1 : une IN_FLIGHT au readback_key MALFORMÉ (clé line_signature absente — torn-write /
    # schéma v3→v4) ne fait PAS fuir de KeyError nu : elle bascule IN_DOUBT + les entrées
    # SUIVANTES sont traitées (corps par entrée hermétique, jamais de boot brické).
    respx.get(f"{_BASE}/ledger_entries").respond(200, json={"items": []})
    store = JsonlStore(tmp_path / "wal.jsonl")
    store.reserve_in_flight(
        _NS,
        "fp-malformed",
        "k1",
        readback_key={"date": "2026-03-31", "journal_id": 42},  # PAS de line_signature
        id_watermark=0,
    )
    _crash_in_flight(store, "fp-valid", id_watermark=0)  # readback_key valide
    async with PennylaneClient(settings=settings) as client:
        report = await _resource(client, store).recover()  # ne lève PAS
    by_fp = {r["fingerprint"]: r for r in report}
    assert by_fp["fp-malformed"]["action"] == "in_doubt_malformed_readback"
    assert by_fp["fp-valid"]["action"] == "in_doubt"  # la suivante traitée malgré le crash
    assert store.get(_NS, "fp-malformed").state is WALState.IN_DOUBT  # type: ignore[union-attr]
    assert store.get(_NS, "fp-valid").state is WALState.IN_DOUBT  # type: ignore[union-attr]


# ─── SC-1/TC-1 : un 201 portant un id déjà CONFIRMED ailleurs (NB3-3) → IN_DOUBT ──


@respx.mock
async def test_create_confirm_conflict_sister_id_in_doubt(
    settings: Settings, tmp_path: Path
) -> None:
    # NB3-3 au niveau create() : un 201 dont l'id est DÉJÀ CONFIRMED sous un AUTRE fingerprint
    # → le store refuse la confirmation (UNIQUE partiel, conflict) → IN_DOUBT, JAMAIS de confirm
    # silencieux ni d'écrasement. fp1 d'origine reste CONFIRMED intact.
    _mock_ledger_get(watermark=0)
    store = SqliteIdempotencyStore(tmp_path / "wal.db")
    store.reserve_in_flight(_NS, "fp-already", "k1")
    store.finalize(_NS, "fp-already", WALState.CONFIRMED, 4242, None)
    post = respx.post(f"{_BASE}/ledger_entries").respond(201, json=_detail(4242))
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(WALInDoubtError) as ei:
            # payload DIFFÉRENT (label) → fingerprint distinct de fp-already ; le POST renvoie
            # néanmoins l'id 4242 déjà confirmé ailleurs.
            await LedgerEntriesResource(client, wal_store=store).create(**_kwargs(label="OD AUTRE"))
    assert ei.value.candidates == [4242]
    assert post.call_count == 1
    first = store.get(_NS, "fp-already")
    assert first is not None and first.state is WALState.CONFIRMED and first.pennylane_id == 4242


# ─── TC-4 : crash mid-recover (erreur store) → store_error, IN_FLIGHT, ré-entrant ─


@respx.mock
async def test_recover_store_error_leaves_in_flight_reentrant(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # TC-4 : une erreur store PENDANT la transition de reprise → action 'store_error', l'entrée
    # reste IN_FLIGHT (jamais d'exception nue). Store rétabli au 2ᵉ recover() → traitée → IN_DOUBT.
    respx.get(f"{_BASE}/ledger_entries").respond(200, json={"items": []})
    store = JsonlStore(tmp_path / "wal.jsonl")
    _crash_in_flight(store, "fp-x", id_watermark=0)
    real_finalize = store.finalize
    calls = {"n": 0}

    def boom_once(*a: object, **k: object) -> WALEntry:
        calls["n"] += 1
        if calls["n"] == 1:
            raise WALStoreError("store down (simulé)", conflict=False)
        return real_finalize(*a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "finalize", boom_once)
    async with PennylaneClient(settings=settings) as client:
        res = LedgerEntriesResource(client, wal_store=store)
        r1 = await res.recover()
        assert r1[0]["action"] == "store_error"
        assert store.get(_NS, "fp-x").state is WALState.IN_FLIGHT  # type: ignore[union-attr]
        r2 = await res.recover()  # store rétabli (boom_once ne lève qu'une fois)
    assert r2[0]["action"] == "in_doubt"
    assert store.get(_NS, "fp-x").state is WALState.IN_DOUBT  # type: ignore[union-attr]


# ─── TC-3 : sœur-devise — readback_key ignore la devise → l'EUR sœur remonte candidat ─


@respx.mock
async def test_currency_sister_readback_surfaces_candidate(
    settings: Settings, tmp_path: Path
) -> None:
    # TC-3 : une écriture EUR (id 100) déjà landée + un create() USD MÊMES LIGNES en timeout →
    # comme la readback_key ignore la devise, l'EUR sœur remonte comme candidat empreinte-
    # readback-identique → IN_DOUBT + candidate 100 (l'humain tranche, jamais d'auto-confirm).
    _mock_ledger_get(watermark=0, list_items=[_list_item(100)])
    post = respx.post(f"{_BASE}/ledger_entries").mock(side_effect=httpx.ReadTimeout("t"))
    respx.get(f"{_BASE}/ledger_entries/100").respond(200, json=_detail(100))
    store = JsonlStore(tmp_path / "wal.jsonl")
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(WALInDoubtError) as ei:
            await _resource(client, store).create(**_kwargs(currency="USD"))
    assert ei.value.candidates == [100]
    assert post.call_count == 1


# ─── NBI-2 : une méthode READ store qui rencontre une erreur driver lève WALStoreError ─


def test_sqlite_read_driver_error_wrapped(tmp_path: Path) -> None:
    # NBI-2 : store construit sur un fichier valide PUIS corrompu → les méthodes READ lèvent
    # WALStoreError (typée), jamais sqlite3.Error nue (sinon recover()/resolve() briqueraient
    # le boot sur une panne SQLite transitoire). conflict=False (lecture).
    db = tmp_path / "wal.db"
    store = SqliteIdempotencyStore(db)  # schéma créé sur un fichier sain
    db.write_bytes(b"not a sqlite database at all")  # corruption post-construction
    with pytest.raises(WALStoreError) as ei_get:
        store.get("ns", "fp")
    assert ei_get.value.conflict is False
    with pytest.raises(WALStoreError):
        store.iter_nonterminal()
    with pytest.raises(WALStoreError):
        store.find_confirmed_fingerprint("ns", 1)


def test_sqlite_malformed_readback_column_wrapped(tmp_path: Path) -> None:
    # NBI-2-RESIDUAL-1 : colonne readback_key au JSON MALFORMÉ sur base SQLite SAINE (torn-write
    # 1 colonne / drift schéma) → le SELECT réussit (pas de sqlite3.Error) mais _row_to_entry
    # (json.loads) lèverait json.JSONDecodeError NU (⊂ ValueError, PAS sqlite3.Error). Doit être
    # enveloppé en WALStoreError, sinon iter_nonterminal fuit → recover() → boot brické.
    import sqlite3 as _sqlite3

    db = tmp_path / "wal.db"
    store = SqliteIdempotencyStore(db)
    store.reserve_in_flight(_NS, "fp", "k", readback_key={"x": 1}, id_watermark=0)
    with _sqlite3.connect(db) as conn:  # base SAINE, on ne corrompt QU'UNE valeur de colonne
        conn.execute(
            "UPDATE wal_entries SET readback_key = ? WHERE fingerprint = ?",
            ("{ this is not valid json", "fp"),
        )
        conn.commit()
    with pytest.raises(WALStoreError) as ei:
        store.get(_NS, "fp")
    assert ei.value.conflict is False
    with pytest.raises(WALStoreError):
        store.iter_nonterminal()  # le chemin de reprise recover() repose là-dessus


def test_sqlite_reserve_corrupt_readback_replay_wrapped(tmp_path: Path) -> None:
    # NBI-2-RESIDUAL-2 : reserve_in_flight relit la colonne readback_key d'une ligne FAILED
    # existante (re-commit) HORS _row_to_entry → site sœur de RESIDUAL-1. Colonne JSON malformée
    # → WALStoreError typé, jamais json.JSONDecodeError nu (qui s'échapperait de create() via le
    # `except BaseException: raise` du store). Fermé par le point UNIQUE _parse_readback_key.
    import sqlite3 as _sqlite3

    db = tmp_path / "wal.db"
    store = SqliteIdempotencyStore(db)
    store.reserve_in_flight(_NS, "fp", "k", readback_key={"x": 1}, id_watermark=0)
    store.finalize(_NS, "fp", WALState.FAILED, None, "4xx")  # ligne FAILED, readback_key préservé
    with _sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE wal_entries SET readback_key = ? WHERE fingerprint = ?",
            ("{ corrupt json", "fp"),
        )
        conn.commit()
    with pytest.raises(WALStoreError) as ei:
        store.reserve_in_flight(_NS, "fp", "k2")  # re-commit → relit la colonne corrompue
    assert ei.value.conflict is False


def test_sqlite_readback_valid_json_non_dict_wrapped(tmp_path: Path) -> None:
    # Belt-and-suspenders (reco C ultra-tight) : colonne readback_key au JSON VALIDE mais
    # NON-objet (liste) → json.loads réussit mais ce n'est pas un dict → WALStoreError (au lieu
    # d'un TypeError plus tard au matching). Ferme la classe désérialisation entière (exception ∪ type).
    import sqlite3 as _sqlite3

    db = tmp_path / "wal.db"
    store = SqliteIdempotencyStore(db)
    store.reserve_in_flight(_NS, "fp", "k", readback_key={"x": 1}, id_watermark=0)
    with _sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE wal_entries SET readback_key = ? WHERE fingerprint = ?",
            ("[1, 2, 3]", "fp"),  # JSON valide, mais liste (pas un objet)
        )
        conn.commit()
    with pytest.raises(WALStoreError):
        store.get(_NS, "fp")


def test_jsonl_read_driver_error_wrapped(tmp_path: Path) -> None:
    # NBI-2 (parité JSONL) : une E/S impossible à la lecture (ici le chemin devient un
    # répertoire → IsADirectoryError, sous-classe OSError) → les méthodes READ lèvent
    # WALStoreError, jamais d'OSError nu (HERM-2 / §2.11). (_load_all tolère déjà les lignes
    # JSON corrompues en les sautant — c'est l'E/S elle-même qu'on protège ici.)
    path = tmp_path / "wal.jsonl"
    store = JsonlStore(path)
    store.reserve_in_flight("ns", "fp", "k")  # crée le fichier
    path.unlink()
    path.mkdir()  # le chemin est désormais un répertoire → open("r") lève IsADirectoryError
    with pytest.raises(WALStoreError):
        store.get("ns", "fp")
    with pytest.raises(WALStoreError):
        store.iter_nonterminal()


@respx.mock
async def test_recover_namespace_filter_multi_dossiers(settings: Settings, tmp_path: Path) -> None:
    # R1 multi-dossiers (2026-06-10) : recover(namespace=X) ne reprend QUE les IN_FLIGHT du
    # dossier X — ceux du dossier Y restent intacts (repris plus tard par SON client/token).
    # Entrées sans readback_key → bascule IN_DOUBT sans AUCUN appel réseau.
    store = JsonlStore(tmp_path / "wal.jsonl")
    store.reserve_in_flight("dossier-A", "fpA", "kA")  # IN_FLIGHT, pas de readback_key
    store.reserve_in_flight("dossier-B", "fpB", "kB")
    async with PennylaneClient(settings=settings) as client:
        res = _resource(client, store)
        report = await res.recover("dossier-A")
    assert [r["fingerprint"] for r in report] == ["fpA"]
    a = store.get("dossier-A", "fpA")
    b = store.get("dossier-B", "fpB")
    assert a is not None and a.state is WALState.IN_DOUBT  # repris (dossier demandé)
    assert b is not None and b.state is WALState.IN_FLIGHT  # INTACT (autre dossier)
