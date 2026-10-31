"""Exécute le contrat partagé (`store_testing.STORE_CONTRACT_CHECKS`) contre les stores
mono-host de la lib publiable (JsonlStore hérité, SqliteIdempotencyStore défaut), + le
test cross-process RÉEL SQLite (revue v2 MF7b) + le garde de déploiement (§2.14).

Le `PostgresStore` app-side réutilise le MÊME contrat (ses propres tests
contre un vrai Postgres) → équivalence de comportement garantie entre stores (ADR).
"""

from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

import pytest

from grandlivre.store_testing import STORE_CONTRACT_CHECKS
from grandlivre.wal import (
    DeploymentUnsafeError,
    JsonlStore,
    ReserveOutcome,
    SqliteIdempotencyStore,
    ensure_deployment_safety,
)


@pytest.fixture(params=["jsonl", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path):
    """Une instance NEUVE et VIDE de chaque store durable mono-host (un store frais par
    check du contrat — isolation par `tmp_path`)."""
    if request.param == "jsonl":
        return JsonlStore(tmp_path / "wal.jsonl")
    return SqliteIdempotencyStore(tmp_path / "wal.db")


@pytest.mark.parametrize("check", STORE_CONTRACT_CHECKS, ids=lambda c: c.__name__)
def test_store_contract(store, check) -> None:  # noqa: ANN001
    """Contrat de comportement partagé — identique pour JSONL, SQLite (et Postgres app-side)."""
    check(store)


def test_mono_host_stores_not_cross_host_safe(store) -> None:  # noqa: ANN001
    assert store.cross_host_safe is False


# --- Garde de démarrage (ADR) ----------------------------------


def test_deployment_guard_blocks_mono_host_multiwriter(tmp_path: Path) -> None:
    sqlite = SqliteIdempotencyStore(tmp_path / "g.db")
    ensure_deployment_safety(sqlite, multi_writer=False)  # mono-machine : OK
    with pytest.raises(DeploymentUnsafeError):
        ensure_deployment_safety(sqlite, multi_writer=True)


class _FakeCrossHostStore:
    """Stub minimal cross-host (p. ex. le PostgresStore) pour le garde."""

    cross_host_safe = True


def test_deployment_guard_allows_cross_host_multiwriter() -> None:
    # Aucune exception : un store cross-host autorise le multi-writer.
    ensure_deployment_safety(_FakeCrossHostStore(), multi_writer=True)  # type: ignore[arg-type]


# --- MF7b : check-and-set atomique cross-PROCESS réel (SQLite) -------------


def _reserve_worker(db_path: str, barrier, queue) -> None:  # noqa: ANN001
    """Process enfant : ouvre SON store sur le même fichier, attend la barrière, réserve."""
    from grandlivre.wal import SqliteIdempotencyStore as _S

    s = _S(db_path)
    barrier.wait()  # tous les process frappent reserve_in_flight quasi simultanément
    res = s.reserve_in_flight("ns", "shared-fp", "k")
    queue.put(res.outcome.value)


def test_sqlite_cross_process_single_proceeding(tmp_path: Path) -> None:
    """N process distincts, MÊME backend SQLite, réservation concurrente du même
    (namespace, fingerprint) → EXACTEMENT 1 PROCEEDING, le reste DUPLICATE.

    Prouve que ``BEGIN IMMEDIATE`` + ``UNIQUE(namespace, fingerprint)`` sérialise les
    writers cross-process sur un même host (revue v2 MF7b) — le garde anti-doublon ne
    repose donc PAS uniquement sur le verrou intra-process ``anyio.Lock``.
    """
    db_path = str(tmp_path / "wal.db")
    SqliteIdempotencyStore(db_path)  # crée le schéma avant le fan-out

    n = 8
    ctx = mp.get_context("spawn")  # portable + isole l'état (pas de fork du runtime test)
    barrier = ctx.Barrier(n)
    queue: mp.Queue = ctx.Queue()
    procs = [ctx.Process(target=_reserve_worker, args=(db_path, barrier, queue)) for _ in range(n)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0

    outcomes = [queue.get(timeout=5) for _ in range(n)]
    assert outcomes.count(ReserveOutcome.PROCEEDING.value) == 1, outcomes
    assert outcomes.count(ReserveOutcome.DUPLICATE.value) == n - 1, outcomes
