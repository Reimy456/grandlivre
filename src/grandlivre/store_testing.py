"""Contrat de comportement réutilisable pour toute implémentation d'``IdempotencyStore``.

Garantit que SQLite (lib), JSONL (lib) et Postgres (store applicatif) ont le MÊME
comportement — la « fausse confiance » d'un store de prod jamais testé est éliminée en
exécutant CE contrat contre chaque implémentation (ADR).

Chaque check prend un ``store`` **frais et vide** (l'appelant fournit une fixture neuve par
check) et lève en cas d'écart. Module pur (zéro dépendance de test) : importable par les
tests de la lib ET par ceux de l'app sans introduire pytest dans le paquet publié.

Usage (pytest) :

    import pytest
    from grandlivre.store_testing import STORE_CONTRACT_CHECKS

    @pytest.mark.parametrize("check", STORE_CONTRACT_CHECKS, ids=lambda c: c.__name__)
    def test_contract(store, check):  # `store` = fixture donnant un store frais
        check(store)
"""

from __future__ import annotations

from collections.abc import Callable

from .wal import IdempotencyStore, ReserveOutcome, WALState, WALStoreError

# Clé de read-back type (forme persistée AVANT le POST, ADR revue v4) : sous-ensemble
# RÉ-OBSERVABLE strict — `label`/`due_date` entrée + signature ligne `[account_id, debit,
# credit, line_label]`. PAS de `currency` (jamais renvoyé par l'API) ni `piece_number` (auto
# Pennylane, EC-2). Opaque pour le store (round-trip JSON) ; la forme n'engage que create().
SAMPLE_READBACK_KEY = {
    "date": "2026-01-15",
    "journal_id": 7,
    "label": "OD CCA",
    "due_date": None,
    "line_signature": [[1, "100.00", "0.00", "CCA"], [2, "0.00", "100.00", ""]],
}
_RB = SAMPLE_READBACK_KEY


def check_reserve_proceeding_persists_readback_and_watermark(
    store: IdempotencyStore,
) -> None:
    res = store.reserve_in_flight("ns", "fp", "k", readback_key=_RB, id_watermark=42)
    assert res.outcome is ReserveOutcome.PROCEEDING
    assert res.entry.state is WALState.IN_FLIGHT
    # readback_key + id_watermark durables AVANT le POST (pour recover()).
    assert res.entry.readback_key == _RB
    assert res.entry.id_watermark == 42
    got = store.get("ns", "fp")
    assert got is not None
    assert got.readback_key == _RB
    assert got.id_watermark == 42


def check_reserve_duplicate_when_in_flight(store: IdempotencyStore) -> None:
    store.reserve_in_flight("ns", "fp", "k1", readback_key=_RB, id_watermark=1)
    res = store.reserve_in_flight("ns", "fp", "k2")
    assert res.outcome is ReserveOutcome.DUPLICATE
    assert res.entry.state is WALState.IN_FLIGHT


def check_reserve_short_circuit_when_confirmed(store: IdempotencyStore) -> None:
    store.reserve_in_flight("ns", "fp", "k", readback_key=_RB, id_watermark=1)
    store.finalize("ns", "fp", WALState.CONFIRMED, 999, None)
    res = store.reserve_in_flight("ns", "fp", "k2")
    assert res.outcome is ReserveOutcome.SHORT_CIRCUIT
    assert res.memorized_id == 999


def check_finalize_preserves_readback_and_watermark(store: IdempotencyStore) -> None:
    store.reserve_in_flight("ns", "fp", "k", readback_key=_RB, id_watermark=77)
    entry = store.finalize("ns", "fp", WALState.IN_DOUBT, None, "boom")
    assert entry.state is WALState.IN_DOUBT
    assert entry.last_error == "boom"
    # finalize NE doit PAS effacer la clé de read-back / le watermark (recover() en a besoin).
    assert entry.readback_key == _RB
    assert entry.id_watermark == 77
    again = store.get("ns", "fp")
    assert again is not None and again.readback_key == _RB and again.id_watermark == 77


def check_get_absent_returns_none(store: IdempotencyStore) -> None:
    assert store.get("ns", "absent") is None


def check_iter_nonterminal_excludes_terminal(store: IdempotencyStore) -> None:
    store.reserve_in_flight("ns", "a", "k", readback_key=_RB, id_watermark=1)  # IN_FLIGHT
    store.reserve_in_flight("ns", "b", "k", readback_key=_RB, id_watermark=1)
    store.finalize("ns", "b", WALState.CONFIRMED, 1, None)  # terminal → exclu
    store.reserve_in_flight("ns", "c", "k", readback_key=_RB, id_watermark=1)
    store.finalize("ns", "c", WALState.IN_DOUBT, None, "x")  # non terminal → inclus
    fps = {e.fingerprint for e in store.iter_nonterminal()}
    assert fps == {"a", "c"}


def check_find_confirmed_fingerprint(store: IdempotencyStore) -> None:
    store.reserve_in_flight("ns", "fp", "k", readback_key=_RB, id_watermark=1)
    store.finalize("ns", "fp", WALState.CONFIRMED, 555, None)
    assert store.find_confirmed_fingerprint("ns", 555) == "fp"
    assert store.find_confirmed_fingerprint("ns", 999) is None
    # Scoping par namespace : un id CONFIRMED dans un autre namespace ne fuit pas.
    assert store.find_confirmed_fingerprint("other", 555) is None


def check_find_confirmed_ignores_nonconfirmed(store: IdempotencyStore) -> None:
    # Un IN_DOUBT portant pennylane_id None ne doit pas matcher la garde écriture-sœur.
    store.reserve_in_flight("ns", "fp", "k", readback_key=_RB, id_watermark=1)
    store.finalize("ns", "fp", WALState.IN_DOUBT, None, "x")
    assert store.find_confirmed_fingerprint("ns", 555) is None


def check_finalize_conflict_sister_write(store: IdempotencyStore) -> None:
    # NB3-3 (ADR) : un même pennylane_id ne peut être CONFIRMED que sous UN seul
    # fingerprint. Confirmer une « écriture-sœur » (autre fingerprint, même id déjà confirmé)
    # → WALStoreError(conflict=True). Parité garantie : JSONL (pré-check explicite),
    # SQLite/Postgres (index UNIQUE partiel) lèvent identiquement — jamais d'écrasement muet.
    store.reserve_in_flight("ns", "fp1", "k1", readback_key=_RB, id_watermark=1)
    store.finalize("ns", "fp1", WALState.CONFIRMED, 4242, None)
    store.reserve_in_flight("ns", "fp2", "k2", readback_key=_RB, id_watermark=1)
    try:
        store.finalize("ns", "fp2", WALState.CONFIRMED, 4242, None)
    except WALStoreError as exc:
        assert exc.conflict is True
    else:
        raise AssertionError("écriture-sœur acceptée — garde NB3-3 absente")
    # L'entrée d'origine reste CONFIRMED intacte (aucun écrasement).
    first = store.get("ns", "fp1")
    assert first is not None and first.state is WALState.CONFIRMED and first.pennylane_id == 4242


def check_transition_confirmed_if_conditional(store: IdempotencyStore) -> None:
    # Revue (c) D2 généralisée (RES-1/RES-2) : transition CONDITIONNELLE par IDENTITÉ,
    # atomique store-level, vers FAILED (ré-écriture) OU IN_DOUBT (résolution humaine).
    # Mauvais expected_pennylane_id → no-op (False) + mapping INTACT ; non-CONFIRMED → False.
    store.reserve_in_flight("ns", "fp", "k", readback_key=_RB, id_watermark=1)
    store.finalize("ns", "fp", WALState.CONFIRMED, 4242, None)
    # Identité périmée (le mapping vit sous un autre id) → REFUS de transitionner.
    assert store.transition_confirmed_if("ns", "fp", 9999, WALState.FAILED, "périmé") is False
    intact = store.get("ns", "fp")
    assert intact is not None
    assert intact.state is WALState.CONFIRMED and intact.pennylane_id == 4242
    # Identité exacte → IN_DOUBT gardé (RES-1 jeune écriture / RES-2 épuisement).
    assert store.transition_confirmed_if("ns", "fp", 4242, WALState.IN_DOUBT, "404 jeune") is True
    doubted = store.get("ns", "fp")
    assert doubted is not None
    assert doubted.state is WALState.IN_DOUBT and doubted.pennylane_id is None
    # Plus CONFIRMED → no-op même avec l'ancien id.
    assert store.transition_confirmed_if("ns", "fp", 4242, WALState.FAILED, "retry") is False
    # Cible FAILED sur une entrée fraîche (chemin invalidation/heal).
    store.reserve_in_flight("ns", "fp2", "k2", readback_key=_RB, id_watermark=1)
    store.finalize("ns", "fp2", WALState.CONFIRMED, 4343, None)
    assert store.transition_confirmed_if("ns", "fp2", 4343, WALState.FAILED, "supprimée") is True
    # Identité « absence d'id » (accusés 204 sans corps, F-1) : un mapping CONFIRMED sans
    # pennylane_id ne se transitionne QUE si l'appelant attend explicitement None.
    store.reserve_in_flight("ns", "fp3", "k3", readback_key=_RB, id_watermark=1)
    store.finalize("ns", "fp3", WALState.CONFIRMED, None, None)
    assert store.transition_confirmed_if("ns", "fp3", 4242, WALState.FAILED, "mauvaise id") is False
    assert store.transition_confirmed_if("ns", "fp3", None, WALState.FAILED, "état périmé") is True
    noid = store.get("ns", "fp3")
    assert noid is not None
    assert noid.state is WALState.FAILED and noid.pennylane_id is None
    # Un mapping AVEC id n'est jamais transitionné par une attente « sans id ».
    store.reserve_in_flight("ns", "fp4", "k4", readback_key=_RB, id_watermark=1)
    store.finalize("ns", "fp4", WALState.CONFIRMED, 4444, None)
    assert store.transition_confirmed_if("ns", "fp4", None, WALState.FAILED, "sans id") is False
    withid = store.get("ns", "fp4")
    assert withid is not None
    assert withid.state is WALState.CONFIRMED and withid.pennylane_id == 4444
    failed = store.get("ns", "fp2")
    assert failed is not None
    assert failed.state is WALState.FAILED and failed.pennylane_id is None


def check_recommit_after_failed_preserves_identity(store: IdempotencyStore) -> None:
    r1 = store.reserve_in_flight("ns", "fp", "k1", readback_key=_RB, id_watermark=10)
    key1 = r1.entry.idempotency_key
    store.finalize("ns", "fp", WALState.FAILED, None, "4xx")
    r2 = store.reserve_in_flight("ns", "fp", "k2")  # re-commit (rien n'a atterri)
    assert r2.outcome is ReserveOutcome.PROCEEDING
    assert r2.entry.idempotency_key == key1  # clé d'idempotence préservée
    assert r2.entry.attempts == 2
    # readback_key + watermark préservés même sans les re-fournir.
    assert r2.entry.readback_key == _RB
    assert r2.entry.id_watermark == 10


# Liste ordonnée du contrat : à exécuter contre CHAQUE implémentation d'IdempotencyStore
# (JsonlStore, SqliteIdempotencyStore, PostgresStore app-side) — même comportement garanti.
STORE_CONTRACT_CHECKS: list[Callable[[IdempotencyStore], None]] = [
    check_reserve_proceeding_persists_readback_and_watermark,
    check_reserve_duplicate_when_in_flight,
    check_reserve_short_circuit_when_confirmed,
    check_finalize_preserves_readback_and_watermark,
    check_get_absent_returns_none,
    check_iter_nonterminal_excludes_terminal,
    check_find_confirmed_fingerprint,
    check_find_confirmed_ignores_nonconfirmed,
    check_finalize_conflict_sister_write,
    check_transition_confirmed_if_conditional,
    check_recommit_after_failed_preserves_identity,
]
