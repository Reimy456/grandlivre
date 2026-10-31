"""Tests du WAL outbox (ADR Cs 3) — fingerprint, store, manager, contention.

Isolation totale (zéro httpx) : prouve les invariants anti-doublon AVANT le POST.
Les scénarios respx (timeout→IN_DOUBT→read-back, 422→FAILED, idempotence rejeu)
sont à Cs 4 (avec `create()`). Couvre §3.4 ADR :
- contention N coroutines même (ns,fp) → 1 seul POST effectif (= C-2/§2.3) ;
- property-based stabilité fingerprint (permutation de lignes, §2.14) ;
- crash durabilité JsonlStore (rename échoué → fichier intact, jamais torn) ;
- CRUX : IN_FLIGHT durable AVANT le POST (§2.14 invariant #1) ;
- classify_for_recovery (IN_FLIGHT → RECONCILE, jamais blind-retry).
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from grandlivre.wal import (
    JsonlStore,
    NullStore,
    RecoveryAction,
    ReserveOutcome,
    WALDuplicatePayloadError,
    WALEntry,
    WALManager,
    WALState,
    WALStoreError,
    classify_for_recovery,
    compute_fingerprint,
)


def _payload(lines: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if lines is None:
        lines = [
            {"ledger_account_id": 486000, "debit": "100.00", "credit": "0"},
            {"ledger_account_id": 606300, "debit": "0", "credit": "100.00"},
        ]
    return {
        "date": "2026-03-31",
        "label": "OD CCA",
        "journal_id": 42,
        "ledger_entry_lines": lines,
    }


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


def test_fingerprint_deterministic() -> None:
    assert compute_fingerprint(_payload()) == compute_fingerprint(_payload())


def test_fingerprint_is_sha256_hex() -> None:
    fp = compute_fingerprint(_payload())
    assert len(fp) == 64
    int(fp, 16)  # hex valide


def test_fingerprint_line_order_invariant() -> None:
    # Multiset : réordonner les lignes ne change pas l'empreinte (§2.14).
    p1 = _payload()
    p2 = _payload(list(reversed(p1["ledger_entry_lines"])))
    assert compute_fingerprint(p1) == compute_fingerprint(p2)


def test_fingerprint_key_order_invariant() -> None:
    p1 = {"date": "2026-03-31", "label": "x", "journal_id": 1, "ledger_entry_lines": []}
    p2 = {"ledger_entry_lines": [], "journal_id": 1, "label": "x", "date": "2026-03-31"}
    assert compute_fingerprint(p1) == compute_fingerprint(p2)


def test_fingerprint_differs_on_amount() -> None:
    p2 = _payload(
        [
            {"ledger_account_id": 486000, "debit": "200.00", "credit": "0"},
            {"ledger_account_id": 606300, "debit": "0", "credit": "200.00"},
        ]
    )
    assert compute_fingerprint(_payload()) != compute_fingerprint(p2)


def test_fingerprint_differs_on_date() -> None:
    p2 = _payload()
    p2["date"] = "2026-04-30"
    assert compute_fingerprint(_payload()) != compute_fingerprint(p2)


@given(
    st.lists(
        st.fixed_dictionaries(
            {
                "ledger_account_id": st.integers(min_value=1, max_value=999999),
                "debit": st.sampled_from(["0", "10.00", "100.50", "7.07"]),
                "credit": st.sampled_from(["0", "10.00", "100.50", "7.07"]),
            }
        ),
        min_size=2,
        max_size=6,
    )
)
def test_fingerprint_stable_under_permutation(lines: list[dict[str, Any]]) -> None:
    # Property : pour toute liste de lignes, la renverser ne change pas le hash.
    shuffled = list(reversed(copy.deepcopy(lines)))
    assert compute_fingerprint(_payload(lines)) == compute_fingerprint(_payload(shuffled))


# ---------------------------------------------------------------------------
# classify_for_recovery (CRUX §2.14)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state, expected",
    [
        (WALState.PENDING, RecoveryAction.RETRY_OK),
        (WALState.IN_FLIGHT, RecoveryAction.RECONCILE),
        (WALState.IN_DOUBT, RecoveryAction.RECONCILE),
        (WALState.CONFIRMED, RecoveryAction.DONE),
        (WALState.FAILED, RecoveryAction.FAILED_TERMINAL),
    ],
)
def test_classify_for_recovery(state: WALState, expected: RecoveryAction) -> None:
    assert classify_for_recovery(state) is expected


# ---------------------------------------------------------------------------
# WALEntry sérialisation
# ---------------------------------------------------------------------------


def test_wal_entry_json_round_trip() -> None:
    entry = WALEntry(
        namespace="d1",
        fingerprint="abc",
        idempotency_key="key-1",
        state=WALState.IN_FLIGHT,
        pennylane_id=None,
        attempts=2,
        created_at="2026-03-31T00:00:00+00:00",
        updated_at="2026-03-31T00:00:01+00:00",
    )
    obj = json.loads(entry.to_json_line())
    assert obj["state"] == "IN_FLIGHT"
    restored = WALEntry.from_json_obj(obj)
    assert restored == entry


# ---------------------------------------------------------------------------
# JsonlStore — reserve_in_flight / finalize / durabilité
# ---------------------------------------------------------------------------


def test_store_reserve_new_proceeds(tmp_path: Path) -> None:
    store = JsonlStore(tmp_path / "wal.jsonl")
    fp = compute_fingerprint(_payload())
    result = store.reserve_in_flight("d1", fp, "key-1")
    assert result.outcome is ReserveOutcome.PROCEEDING
    # IN_FLIGHT durable immédiatement (CRUX §2.14).
    persisted = store.get("d1", fp)
    assert persisted is not None and persisted.state is WALState.IN_FLIGHT


def test_store_confirmed_short_circuits(tmp_path: Path) -> None:
    store = JsonlStore(tmp_path / "wal.jsonl")
    fp = compute_fingerprint(_payload())
    store.reserve_in_flight("d1", fp, "key-1")
    store.finalize("d1", fp, WALState.CONFIRMED, 777, None)
    result = store.reserve_in_flight("d1", fp, "key-2")
    assert result.outcome is ReserveOutcome.SHORT_CIRCUIT
    assert result.memorized_id == 777


def test_store_in_flight_is_duplicate(tmp_path: Path) -> None:
    store = JsonlStore(tmp_path / "wal.jsonl")
    fp = compute_fingerprint(_payload())
    store.reserve_in_flight("d1", fp, "key-1")
    result = store.reserve_in_flight("d1", fp, "key-2")
    assert result.outcome is ReserveOutcome.DUPLICATE


def test_store_in_doubt_is_duplicate(tmp_path: Path) -> None:
    store = JsonlStore(tmp_path / "wal.jsonl")
    fp = compute_fingerprint(_payload())
    store.reserve_in_flight("d1", fp, "key-1")
    store.finalize("d1", fp, WALState.IN_DOUBT, None, "timeout")
    result = store.reserve_in_flight("d1", fp, "key-2")
    assert result.outcome is ReserveOutcome.DUPLICATE


def test_store_failed_reproceeds(tmp_path: Path) -> None:
    # FAILED = rien n'a atterri (422 refusé) → ré-essai autorisé, attempts++.
    store = JsonlStore(tmp_path / "wal.jsonl")
    fp = compute_fingerprint(_payload())
    store.reserve_in_flight("d1", fp, "key-1")
    store.finalize("d1", fp, WALState.FAILED, None, "422")
    result = store.reserve_in_flight("d1", fp, "key-2")
    assert result.outcome is ReserveOutcome.PROCEEDING
    assert result.entry.attempts == 2
    # clé d'idempotence stable (réutilise l'originale, pas key-2).
    assert result.entry.idempotency_key == "key-1"


def test_store_namespace_isolation(tmp_path: Path) -> None:
    store = JsonlStore(tmp_path / "wal.jsonl")
    fp = compute_fingerprint(_payload())  # MÊME fingerprint
    store.reserve_in_flight("dossierA", fp, "kA")
    store.finalize("dossierA", fp, WALState.CONFIRMED, 1, None)
    # dossierB, même payload → indépendant (pas de faux-doublon cross-dossier).
    result = store.reserve_in_flight("dossierB", fp, "kB")
    assert result.outcome is ReserveOutcome.PROCEEDING
    assert store.get("dossierA", fp).state is WALState.CONFIRMED  # type: ignore[union-attr]
    assert store.get("dossierB", fp).state is WALState.IN_FLIGHT  # type: ignore[union-attr]


def test_store_torn_write_tolerance(tmp_path: Path) -> None:
    path = tmp_path / "wal.jsonl"
    good = WALEntry(
        namespace="d1",
        fingerprint="fp1",
        idempotency_key="k1",
        state=WALState.CONFIRMED,
        pennylane_id=5,
    )
    # Écrit une ligne valide + une ligne tronquée/corrompue.
    path.write_text(good.to_json_line() + "\n" + '{"namespace":"d1","fing\n', encoding="utf-8")
    store = JsonlStore(path)
    # La ligne corrompue est ignorée ; la bonne survit.
    assert store.get("d1", "fp1") is not None
    assert store.get("d1", "fp1").pennylane_id == 5  # type: ignore[union-attr]


def test_store_crash_durability_rename_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1 entrée CONFIRMED durable, puis rename échoue sur une 2e écriture →
    # le fichier original reste intact (rename atomique : tout-ou-rien, §2.14).
    store = JsonlStore(tmp_path / "wal.jsonl")
    fp1 = compute_fingerprint(_payload())
    store.reserve_in_flight("d1", fp1, "k1")
    store.finalize("d1", fp1, WALState.CONFIRMED, 99, None)

    real_rename = os.rename

    def boom(src: Any, dst: Any) -> None:
        raise OSError("disk full (simulé)")

    monkeypatch.setattr(os, "rename", boom)
    fp2 = compute_fingerprint(
        _payload(
            [
                {"ledger_account_id": 1, "debit": "5.00", "credit": "0"},
                {"ledger_account_id": 2, "debit": "0", "credit": "5.00"},
            ]
        )
    )
    # HERM-2 : l'OSError d'E/S est enveloppé en WALStoreError (jamais nu, §2.11), conflict=False
    # (E/S ratée ≠ conflit d'unicité). La cause d'origine reste chaînée.
    with pytest.raises(WALStoreError) as ei:
        store.reserve_in_flight("d1", fp2, "k2")
    assert isinstance(ei.value.__cause__, OSError)
    assert ei.value.conflict is False
    monkeypatch.setattr(os, "rename", real_rename)

    # L'entrée originale est intacte ; la 2e n'a jamais atterri (jamais torn).
    assert store.get("d1", fp1).state is WALState.CONFIRMED  # type: ignore[union-attr]
    assert store.get("d1", fp2) is None


def test_store_fsync_called(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Prouve que fsync (fd + répertoire) est invoqué à la persistance (§2.7).
    calls: list[int] = []
    real_fsync = os.fsync

    def counting_fsync(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", counting_fsync)
    store = JsonlStore(tmp_path / "wal.jsonl")
    store.reserve_in_flight("d1", compute_fingerprint(_payload()), "k1")
    assert len(calls) >= 2  # fsync(fd fichier) + fsync(fd répertoire)


# ---------------------------------------------------------------------------
# NullStore
# ---------------------------------------------------------------------------


def test_nullstore_warns() -> None:
    with pytest.warns(UserWarning, match="idempotence"):
        NullStore()


def test_nullstore_always_proceeds_no_persistence() -> None:
    with pytest.warns(UserWarning):
        store = NullStore()
    fp = compute_fingerprint(_payload())
    assert store.reserve_in_flight("d1", fp, "k1").outcome is ReserveOutcome.PROCEEDING
    assert store.get("d1", fp) is None  # aucune persistance


# ---------------------------------------------------------------------------
# WALManager (async)
# ---------------------------------------------------------------------------


async def test_manager_reserve_proceeds_in_flight_durable_before_post(tmp_path: Path) -> None:
    store = JsonlStore(tmp_path / "wal.jsonl")
    mgr = WALManager(store)
    payload = _payload()
    fp = compute_fingerprint(payload)
    async with mgr.reserve(namespace="d1", payload=payload) as resv:
        assert resv.should_proceed
        # CRUX §2.14 : AVANT tout POST, IN_FLIGHT est déjà durable sur disque.
        assert store.get("d1", fp).state is WALState.IN_FLIGHT  # type: ignore[union-attr]
        await resv.confirm(123)
    assert store.get("d1", fp).state is WALState.CONFIRMED  # type: ignore[union-attr]


async def test_manager_reserve_short_circuit(tmp_path: Path) -> None:
    store = JsonlStore(tmp_path / "wal.jsonl")
    mgr = WALManager(store)
    payload = _payload()
    fp = compute_fingerprint(payload)
    store.reserve_in_flight("d1", fp, "k1")
    store.finalize("d1", fp, WALState.CONFIRMED, 555, None)
    async with mgr.reserve(namespace="d1", payload=payload) as resv:
        assert resv.should_short_circuit
        assert resv.memorized_id == 555


async def test_manager_reserve_duplicate(tmp_path: Path) -> None:
    store = JsonlStore(tmp_path / "wal.jsonl")
    mgr = WALManager(store)
    payload = _payload()
    fp = compute_fingerprint(payload)
    store.reserve_in_flight("d1", fp, "k1")  # IN_FLIGHT déjà posé
    async with mgr.reserve(namespace="d1", payload=payload) as resv:
        assert resv.is_duplicate_in_progress
        assert resv.duplicate_state is WALState.IN_FLIGHT
        # L'appelant lèverait WALDuplicatePayloadError ici.
        exc = WALDuplicatePayloadError(fingerprint=resv.fingerprint, wal_state=resv.duplicate_state)
        assert exc.wal_state is WALState.IN_FLIGHT
        assert exc.status_code is None


async def test_manager_transitions_fail_and_in_doubt(tmp_path: Path) -> None:
    store = JsonlStore(tmp_path / "wal.jsonl")
    mgr = WALManager(store)
    p1, p2 = (
        _payload(),
        _payload(
            [
                {"ledger_account_id": 1, "debit": "9.00", "credit": "0"},
                {"ledger_account_id": 2, "debit": "0", "credit": "9.00"},
            ]
        ),
    )
    async with mgr.reserve(namespace="d1", payload=p1) as resv:
        await resv.fail("422 journal closed")
    async with mgr.reserve(namespace="d1", payload=p2) as resv:
        await resv.mark_in_doubt("timeout")
    assert store.get("d1", compute_fingerprint(p1)).state is WALState.FAILED  # type: ignore[union-attr]
    assert store.get("d1", compute_fingerprint(p2)).state is WALState.IN_DOUBT  # type: ignore[union-attr]


async def test_manager_contention_single_post(tmp_path: Path) -> None:
    # === C-2 / §2.3 : N coroutines même (ns,fp) -> 1 SEUL POST effectif. ===
    store = JsonlStore(tmp_path / "wal.jsonl")
    mgr = WALManager(store)
    payload = _payload()
    fp = compute_fingerprint(payload)
    post_count = 0

    async def fake_post() -> None:
        nonlocal post_count
        await asyncio.sleep(0)  # yield : exposerait une race si le verrou était cassé
        post_count += 1

    async def worker() -> str:
        async with mgr.reserve(namespace="d1", payload=payload) as resv:
            if resv.should_proceed:
                await fake_post()
                await resv.confirm(777)
                return "posted"
            if resv.should_short_circuit:
                return "short"
            return "dup"

    results = await asyncio.gather(*[worker() for _ in range(8)])
    assert post_count == 1  # un seul POST malgré 8 coroutines concurrentes
    assert results.count("posted") == 1
    # le verrou + court-circuit CONFIRMED garantissent zéro doublon.
    assert all(r in ("posted", "short") for r in results)
    assert store.get("d1", fp).state is WALState.CONFIRMED  # type: ignore[union-attr]


async def test_manager_different_fingerprint_both_proceed(tmp_path: Path) -> None:
    store = JsonlStore(tmp_path / "wal.jsonl")
    mgr = WALManager(store)
    p1 = _payload()
    p2 = _payload(
        [
            {"ledger_account_id": 1, "debit": "3.00", "credit": "0"},
            {"ledger_account_id": 2, "debit": "0", "credit": "3.00"},
        ]
    )
    async with mgr.reserve(namespace="d1", payload=p1) as r1:
        assert r1.should_proceed
    async with mgr.reserve(namespace="d1", payload=p2) as r2:
        assert r2.should_proceed  # fingerprint distinct → pas de faux-doublon


async def test_manager_lock_registry_never_removed(tmp_path: Path) -> None:
    store = JsonlStore(tmp_path / "wal.jsonl")
    mgr = WALManager(store)
    payload = _payload()
    lock_a = await mgr._lock_for("d1", "fp-x")
    lock_b = await mgr._lock_for("d1", "fp-x")
    assert lock_a is lock_b  # même clé → même verrou (jamais retiré, §2.14)
    async with mgr.reserve(namespace="d1", payload=payload) as resv:
        await resv.confirm(1)
    # après usage, le verrou reste dans le registre (pas de retrait naïf).
    key = "d1\x00" + compute_fingerprint(payload)
    assert key in mgr._locks


# ---------------------------------------------------------------------------
# Garde-fous resolve() + anti-orphelin reserve() (revue adversariale Cs 4 #3c/#6)
# ---------------------------------------------------------------------------


async def test_resolve_requires_pennylane_id_when_landed(tmp_path: Path) -> None:
    # F1 (review locale 2026-06-20) : `pennylane_id` exigé pour `landed=True` SUR UNE ÉCRITURE
    # À ID SERVEUR (readback_key présent = ledger). On seed une entrée ledger IN_DOUBT. Les ops
    # ack-only 204-no-id (readback_key None) confirment sans id : cf.
    # test_invoice_payments::test_resolve_landed_no_id_ok_for_payment_blocked_for_ledger.
    store = JsonlStore(tmp_path / "wal.jsonl")
    mgr = WALManager(store)
    store.reserve_in_flight(
        "d1",
        "fp",
        "k1",
        readback_key={"date": "2026-01-01", "journal_id": 1, "line_signature": []},
    )
    store.finalize("d1", "fp", WALState.IN_DOUBT, None, "doute technique")
    with pytest.raises(ValueError, match="pennylane_id"):
        await mgr.resolve(namespace="d1", fingerprint="fp", landed=True, pennylane_id=None)


async def test_resolve_rejects_non_in_doubt(tmp_path: Path) -> None:
    # resolve() sur une entrée IN_FLIGHT (pas IN_DOUBT) → refus (jamais ex-nihilo).
    store = JsonlStore(tmp_path / "wal.jsonl")
    mgr = WALManager(store)
    fp = compute_fingerprint(_payload())
    store.reserve_in_flight("d1", fp, "k1")  # état IN_FLIGHT
    with pytest.raises(ValueError, match="non-IN_DOUBT|rien à résoudre"):
        await mgr.resolve(namespace="d1", fingerprint=fp, landed=False)


async def test_resolve_absent_entry_rejected(tmp_path: Path) -> None:
    # resolve() sur un fingerprint jamais réservé → refus (pas de CONFIRMED ex-nihilo).
    mgr = WALManager(JsonlStore(tmp_path / "wal.jsonl"))
    with pytest.raises(ValueError, match="absente|rien à résoudre"):
        await mgr.resolve(namespace="d1", fingerprint="jamais-vu", landed=True, pennylane_id=9)


async def test_reserve_orphan_guard_marks_in_doubt(tmp_path: Path) -> None:
    # Sortie du bloc reserve() via exception non gérée, entrée encore IN_FLIGHT (aucune
    # transition) → le garde finally bascule IN_DOUBT — JAMAIS d'IN_FLIGHT orphelin.
    store = JsonlStore(tmp_path / "wal.jsonl")
    mgr = WALManager(store)
    payload = _payload()
    fp = compute_fingerprint(payload)
    with pytest.raises(RuntimeError):
        async with mgr.reserve(namespace="d1", payload=payload) as resv:
            assert resv.should_proceed
            raise RuntimeError("boom — aucune transition appelée")
    entry = store.get("d1", fp)
    assert entry is not None and entry.state is WALState.IN_DOUBT


async def test_reserve_finalize_failure_during_confirm_not_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Medium 1 (revue v2) : si store.finalize ÉCHOUE pendant confirm(), _resolved n'est PAS
    # posé (il l'est APRÈS le succès durable) → la garde finally rattrape → IN_DOUBT. Jamais
    # d'IN_FLIGHT orphelin ni de CONFIRMED fantôme sur un finalize raté.
    store = JsonlStore(tmp_path / "wal.jsonl")
    mgr = WALManager(store)
    payload = _payload()
    fp = compute_fingerprint(payload)
    real_finalize = store.finalize

    def flaky_finalize(
        namespace: str,
        fingerprint: str,
        state: WALState,
        pennylane_id: int | None,
        last_error: str | None,
    ) -> WALEntry:
        if state is WALState.CONFIRMED:
            raise OSError("disque plein pendant le finalize CONFIRMED")
        return real_finalize(namespace, fingerprint, state, pennylane_id, last_error)

    monkeypatch.setattr(store, "finalize", flaky_finalize)

    with pytest.raises(OSError):
        async with mgr.reserve(namespace="d1", payload=payload) as resv:
            assert resv.should_proceed
            await resv.confirm(999)  # finalize CONFIRMED échoue → _resolved reste False
    entry = store.get("d1", fp)
    # La garde finally a basculé IN_DOUBT : ni IN_FLIGHT orphelin, ni CONFIRMED fantôme.
    assert entry is not None and entry.state is WALState.IN_DOUBT
