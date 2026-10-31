"""Property-based (hypothesis) — invariants du WAL anti-doublon (loop juges cran 2).

L'empreinte est LA clé d'idempotence (« même empreinte → jamais 2 POST ») : on attaque
ses invariants + la réservation single-PROCEEDING + la transition conditionnelle par
identité, sur des entrées ALÉATOIRES.
"""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from grandlivre.wal import (
    ReserveOutcome,
    SqliteIdempotencyStore,
    WALState,
    compute_fingerprint,
)

_label = st.text(min_size=0, max_size=24)
_amount = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("99999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def payloads(draw: st.DrawFn) -> dict:
    """Payload create-entry réaliste : date/label/journal + lignes multiset."""
    nb = draw(st.integers(1, 4))
    lines = [
        {
            "ledger_account_id": draw(st.integers(1, 10_000)),
            "label": draw(_label),
            "debit": str(draw(_amount)),
            "credit": "0.00",
        }
        for _ in range(nb)
    ]
    return {
        "date": draw(st.dates()).isoformat(),
        "label": draw(_label),
        "journal_id": draw(st.integers(1, 999)),
        "ledger_entry_lines": lines,
    }


@settings(max_examples=150, deadline=None)
@given(payload=payloads(), seed=st.randoms())
def test_fingerprint_invariant_ordre_cles_et_lignes(payload: dict, seed) -> None:
    # Même contenu, ordre d'insertion des clés + ordre des lignes mélangés →
    # MÊME empreinte (le multiset de lignes n'est pas sémantique en double-partie).
    fp = compute_fingerprint(payload)
    melange = dict(reversed(list(payload.items())))
    lignes = list(payload["ledger_entry_lines"])
    seed.shuffle(lignes)
    melange["ledger_entry_lines"] = [dict(reversed(list(ln.items()))) for ln in lignes]
    assert compute_fingerprint(melange) == fp


@settings(max_examples=100, deadline=None)
@given(payload=payloads(), delta=_amount)
def test_fingerprint_detecte_tout_changement_de_montant(payload: dict, delta: Decimal) -> None:
    fp = compute_fingerprint(payload)
    autre = dict(payload)
    lignes = [dict(ln) for ln in payload["ledger_entry_lines"]]
    nouveau = str(Decimal(lignes[0]["debit"]) + delta)
    lignes[0] = {**lignes[0], "debit": nouveau}
    autre["ledger_entry_lines"] = lignes
    assert compute_fingerprint(autre) != fp  # un centime de différence = autre écriture


@settings(max_examples=40, deadline=None)
@given(tentatives=st.integers(2, 6), fp=st.text(min_size=8, max_size=40))
def test_single_proceeding_sous_reservations_repetees(tentatives: int, fp: str) -> None:
    # Quel que soit le nombre de réservations sur la même empreinte : EXACTEMENT UNE
    # est PROCEEDING (l'unique écrivain), toutes les autres = DUPLICATE (zéro 2e POST) ;
    # un CONFIRMED ultérieur court-circuite avec l'id mémorisé ; un FAILED ré-ouvre UNE
    # place exactement (re-soumission).
    rb = {"date": "2026-06-15", "journal_id": 1}
    with tempfile.TemporaryDirectory() as tmp:
        store = SqliteIdempotencyStore(Path(tmp) / "wal.db")
        issues = [
            store.reserve_in_flight("ns", fp, "k", readback_key=rb, id_watermark=1).outcome
            for _ in range(tentatives)
        ]
        assert issues[0] is ReserveOutcome.PROCEEDING
        assert all(o is ReserveOutcome.DUPLICATE for o in issues[1:])
        # CONFIRMED → toute réservation suivante court-circuite avec l'id mémorisé.
        store.finalize("ns", fp, WALState.CONFIRMED, 4242, None)
        court = store.reserve_in_flight("ns", fp, "k", readback_key=rb, id_watermark=1)
        assert court.outcome is ReserveOutcome.SHORT_CIRCUIT and court.memorized_id == 4242
        # FAILED (via transition gardée) → exactement UNE place ré-ouverte.
        assert store.transition_confirmed_if("ns", fp, 4242, WALState.FAILED, "test") is True
        again = [
            store.reserve_in_flight("ns", fp, "k", readback_key=rb, id_watermark=1).outcome
            for _ in range(2)
        ]
        assert again == [ReserveOutcome.PROCEEDING, ReserveOutcome.DUPLICATE]


@settings(max_examples=100, deadline=None)
@given(
    vrai_id=st.integers(1, 10**9),
    pretendu=st.integers(1, 10**9),
    cible=st.sampled_from([WALState.FAILED, WALState.IN_DOUBT]),
)
def test_transition_conditionnelle_jamais_d_ecrasement_aveugle(
    vrai_id: int, pretendu: int, cible: WALState
) -> None:
    # La transition gardée ne réussit QUE si l'identité prétendue est l'identité vraie ;
    # sinon le mapping CONFIRMED reste STRICTEMENT intact (D2 / RES-1 / RES-2).
    with tempfile.TemporaryDirectory() as tmp:
        store = SqliteIdempotencyStore(Path(tmp) / "wal.db")
        store.reserve_in_flight(
            "ns", "fp", "k", readback_key={"date": "2026-06-15", "journal_id": 1}, id_watermark=1
        )
        store.finalize("ns", "fp", WALState.CONFIRMED, vrai_id, None)
        ok = store.transition_confirmed_if("ns", "fp", pretendu, cible, "prop")
        entry = store.get("ns", "fp")
        assert entry is not None
        if pretendu == vrai_id:
            assert ok is True and entry.state is cible and entry.pennylane_id is None
        else:
            assert ok is False
            assert entry.state is WALState.CONFIRMED and entry.pennylane_id == vrai_id


def test_transition_conditionnelle_identite_sans_id() -> None:
    # Backport grandlivre (F-1) : un write 204-no-id confirmé SANS id (rapprochement, statut)
    # doit pouvoir être invalidé par la transition gardée avec `expected_pennylane_id=None`
    # (`pennylane_id IS ?` → IS NULL) ; avec `= ?`, `NULL = NULL` n'atteignait jamais la ligne.
    rb = {"date": "2026-06-15", "journal_id": 1}
    with tempfile.TemporaryDirectory() as tmp:
        store = SqliteIdempotencyStore(Path(tmp) / "wal.db")
        store.reserve_in_flight("ns", "fp", "k", readback_key=rb, id_watermark=1)
        store.finalize("ns", "fp", WALState.CONFIRMED, None, None)
        # une identité prétendue (un id) ne touche pas un mapping confirmé sans id
        assert store.transition_confirmed_if("ns", "fp", 4242, WALState.IN_DOUBT, "x") is False
        assert store.get("ns", "fp").state is WALState.CONFIRMED
        # l'absence d'id EST l'identité attendue : la transition passe, une seule ligne
        assert (
            store.transition_confirmed_if("ns", "fp", None, WALState.IN_DOUBT, "relecture") is True
        )
        entry = store.get("ns", "fp")
        assert entry is not None and entry.state is WALState.IN_DOUBT and entry.pennylane_id is None
        # symétrique : un mapping confirmé AVEC id n'est pas atteint par `None`
        store.reserve_in_flight("ns", "fp2", "k", readback_key=rb, id_watermark=1)
        store.finalize("ns", "fp2", WALState.CONFIRMED, 77, None)
        assert store.transition_confirmed_if("ns", "fp2", None, WALState.FAILED, "x") is False
        assert store.get("ns", "fp2").pennylane_id == 77
