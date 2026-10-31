"""Tests des modèles d'entrée write (ADR Cs 2) — NewLedgerEntry / NewLedgerEntryLine.

Couvre : invariant XOR débit/crédit, non-négativité, sérialisation Decimal->str
(mode JSON), validation date ISO stricte, >= 2 lignes, label non vide,
`extra="forbid"`, exclusion des optionnels None, et la frontière de design
ADR (le modèle accepte une écriture déséquilibrée — c'est `create()`
qui la refuse, pas le modèle).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from grandlivre import (
    CreatedLedgerEntry,
    NewLedgerEntry,
    NewLedgerEntryLine,
)

# ---------------------------------------------------------------------------
# NewLedgerEntryLine — invariant XOR + non-négativité + types de montant
# ---------------------------------------------------------------------------


def test_line_debit_only_valid() -> None:
    line = NewLedgerEntryLine(ledger_account_id=606300, debit=Decimal("100.00"))
    assert line.debit == Decimal("100.00")
    assert line.credit == Decimal("0")
    assert line.label is None


def test_line_credit_only_valid() -> None:
    line = NewLedgerEntryLine(ledger_account_id=486000, credit=Decimal("100.00"), label="CCA")
    assert line.credit == Decimal("100.00")
    assert line.debit == Decimal("0")
    assert line.label == "CCA"


def test_line_both_positive_rejected() -> None:
    with pytest.raises(ValidationError, match="exactement un"):
        NewLedgerEntryLine(ledger_account_id=1, debit=Decimal("10"), credit=Decimal("5"))


def test_line_both_zero_rejected() -> None:
    with pytest.raises(ValidationError, match="exactement un"):
        NewLedgerEntryLine(ledger_account_id=1)  # debit=0, credit=0 par défaut


def test_line_negative_debit_rejected() -> None:
    with pytest.raises(ValidationError, match=">= 0"):
        NewLedgerEntryLine(ledger_account_id=1, debit=Decimal("-10"))


def test_line_accepts_string_amount() -> None:
    line = NewLedgerEntryLine(ledger_account_id=1, debit="100.00")
    assert line.debit == Decimal("100.00")


def test_line_accepts_int_amount() -> None:
    line = NewLedgerEntryLine(ledger_account_id=1, credit=50)
    assert line.credit == Decimal("50")


def test_line_rejects_float_amount() -> None:
    # parse_decimal refuse float (perte de précision IEEE 754).
    with pytest.raises((TypeError, ValidationError)):
        NewLedgerEntryLine(ledger_account_id=1, debit=100.0)


def test_line_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        NewLedgerEntryLine(ledger_account_id=1, debit=Decimal("10"), unknown_field="x")


def test_line_amount_serialized_as_string_json() -> None:
    line = NewLedgerEntryLine(ledger_account_id=1, debit=Decimal("100.00"))
    dumped = line.model_dump(mode="json")
    assert dumped["debit"] == "100.00"
    assert dumped["credit"] == "0"
    assert isinstance(dumped["debit"], str)


def test_line_amount_python_mode_stays_decimal() -> None:
    line = NewLedgerEntryLine(ledger_account_id=1, debit=Decimal("100.00"))
    dumped = line.model_dump()  # mode python
    assert dumped["debit"] == Decimal("100.00")


# ---------------------------------------------------------------------------
# NewLedgerEntry — structure, dates, extra=forbid, payload JSON
# ---------------------------------------------------------------------------


def _balanced_lines() -> list[NewLedgerEntryLine]:
    return [
        NewLedgerEntryLine(ledger_account_id=486000, debit=Decimal("100.00")),
        NewLedgerEntryLine(ledger_account_id=606300, credit=Decimal("100.00")),
    ]


def test_entry_valid_minimal() -> None:
    entry = NewLedgerEntry(
        date="2026-03-31",
        label="OD CCA",
        journal_id=42,
        ledger_entry_lines=_balanced_lines(),
    )
    assert entry.journal_id == 42
    assert len(entry.ledger_entry_lines) == 2


def test_entry_requires_two_lines() -> None:
    with pytest.raises(ValidationError, match=">= 2 lignes"):
        NewLedgerEntry(
            date="2026-03-31",
            label="x",
            journal_id=1,
            ledger_entry_lines=[NewLedgerEntryLine(ledger_account_id=1, debit=Decimal("10"))],
        )


def test_entry_empty_label_rejected() -> None:
    with pytest.raises(ValidationError, match="vide"):
        NewLedgerEntry(
            date="2026-03-31",
            label="   ",
            journal_id=1,
            ledger_entry_lines=_balanced_lines(),
        )


def test_entry_bad_date_format_rejected() -> None:
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        NewLedgerEntry(
            date="31/03/2026",
            label="x",
            journal_id=1,
            ledger_entry_lines=_balanced_lines(),
        )


def test_entry_invalid_calendar_date_rejected() -> None:
    with pytest.raises(ValidationError, match="hors plage"):
        NewLedgerEntry(
            date="2026-13-01",
            label="x",
            journal_id=1,
            ledger_entry_lines=_balanced_lines(),
        )


def test_entry_due_date_valid() -> None:
    entry = NewLedgerEntry(
        date="2026-03-31",
        due_date="2026-04-30",
        label="x",
        journal_id=1,
        ledger_entry_lines=_balanced_lines(),
    )
    assert entry.due_date == "2026-04-30"


def test_entry_due_date_bad_rejected() -> None:
    with pytest.raises(ValidationError):
        NewLedgerEntry(
            date="2026-03-31",
            due_date="nope",
            label="x",
            journal_id=1,
            ledger_entry_lines=_balanced_lines(),
        )


def test_entry_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        NewLedgerEntry(
            date="2026-03-31",
            label="x",
            journal_id=1,
            ledger_entry_lines=_balanced_lines(),
            typo_field="boom",
        )


def test_entry_optional_fields_accepted() -> None:
    entry = NewLedgerEntry(
        date="2026-03-31",
        label="x",
        journal_id=1,
        ledger_entry_lines=_balanced_lines(),
        currency="EUR",
        piece_number="P-001",
        ledger_attachment_id=7,
        file_attachment_id=9,
    )
    assert entry.currency == "EUR"
    assert entry.piece_number == "P-001"
    assert entry.ledger_attachment_id == 7
    assert entry.file_attachment_id == 9


def test_entry_payload_json_shape() -> None:
    entry = NewLedgerEntry(
        date="2026-03-31",
        label="OD CCA",
        journal_id=42,
        ledger_entry_lines=_balanced_lines(),
        currency="EUR",
    )
    payload = entry.model_dump(mode="json", exclude_none=True)
    assert payload["date"] == "2026-03-31"
    assert payload["label"] == "OD CCA"
    assert payload["journal_id"] == 42
    assert payload["currency"] == "EUR"
    assert "due_date" not in payload  # None exclu du payload
    assert "piece_number" not in payload
    lines = payload["ledger_entry_lines"]
    assert lines[0]["ledger_account_id"] == 486000
    assert lines[0]["debit"] == "100.00"  # Decimal -> str
    assert lines[0]["credit"] == "0"
    assert "label" not in lines[0]  # None exclu
    assert lines[1]["credit"] == "100.00"


def test_model_allows_unbalanced_entry_balance_is_create_gate() -> None:
    # ADR : le modèle porte la FORME ; l'équilibre Σdébit == Σcrédit
    # est la garde de create() (UnbalancedEntryError), pas du modèle.
    entry = NewLedgerEntry(
        date="2026-03-31",
        label="déséquilibrée",
        journal_id=1,
        ledger_entry_lines=[
            NewLedgerEntryLine(ledger_account_id=1, debit=Decimal("100")),
            NewLedgerEntryLine(ledger_account_id=2, credit=Decimal("90")),
        ],
    )
    assert len(entry.ledger_entry_lines) == 2  # construit malgré le déséquilibre


# ---------------------------------------------------------------------------
# CreatedLedgerEntry — réponse 201 (id en-tête + id lignes serveur)
# ---------------------------------------------------------------------------


def test_created_ledger_entry_parse_201() -> None:
    # Parse une réponse 201 (shape R-code-1 OpenAPI) : id en-tête + id lignes serveur
    # + montants string -> Decimal.
    payload = {
        "id": 12345,
        "label": "OD CCA",
        "date": "2026-03-31",
        "journal_id": 42,
        "journal": {"id": 42, "code": "OD"},
        "piece_number": "P-001",
        "ledger_entry_lines": [
            {"id": 9001, "ledger_account_id": 486000, "debit": "100.00", "credit": "0.0"},
            {
                "id": 9002,
                "ledger_account_id": 606300,
                "debit": "0.0",
                "credit": "100.00",
                "ledger_account": {"id": 606300},
            },
        ],
    }
    created = CreatedLedgerEntry.model_validate(payload)
    assert created.id == 12345
    assert created.journal_id == 42
    assert len(created.ledger_entry_lines) == 2
    line0 = created.ledger_entry_lines[0]
    assert line0.id == 9001
    assert line0.ledger_account_id == 486000
    assert line0.debit == Decimal("100.00")  # string API -> Decimal
    assert created.ledger_entry_lines[1].credit == Decimal("100.00")


def test_created_ledger_entry_requires_id() -> None:
    with pytest.raises(ValidationError):
        CreatedLedgerEntry.model_validate({"label": "sans id"})


def test_created_ledger_entry_extra_allow_resilience() -> None:
    # extra="allow" : champ API non modélisé préservé (résilience Sunset 2026) ;
    # ledger_entry_lines absent -> défaut [].
    created = CreatedLedgerEntry.model_validate({"id": 7, "champ_api_futur": "x"})
    assert created.id == 7
    assert created.ledger_entry_lines == []
