"""Tests du parsing strict des montants Pennylane."""

from __future__ import annotations

from decimal import Decimal

import pytest

from grandlivre.parsing import parse_decimal


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("0", Decimal("0")),
        ("0.00", Decimal("0.00")),
        ("1200.50", Decimal("1200.50")),
        ("-42.10", Decimal("-42.10")),
        (1200, Decimal("1200")),
        (0, Decimal("0")),
        (-7, Decimal("-7")),
    ],
)
def test_parse_decimal_canonical(raw: object, expected: Decimal) -> None:
    assert parse_decimal(raw) == expected


def test_parse_decimal_passthrough_decimal() -> None:
    d = Decimal("3.14")
    assert parse_decimal(d) is d


def test_parse_decimal_invalid_string() -> None:
    with pytest.raises(ValueError, match="Montant invalide"):
        parse_decimal("not-a-number")


def test_parse_decimal_missing_field_explicit_error() -> None:
    with pytest.raises(ValueError, match="Champ montant absent"):
        parse_decimal(None)


def test_parse_decimal_rejects_float() -> None:
    """Refus explicite : float interdit (IEEE 754 perte de précision)."""
    with pytest.raises(TypeError, match="float"):
        parse_decimal(1200.50)


def test_parse_decimal_rejects_bool() -> None:
    """bool est sous-classe de int : refus explicite (ambiguïté)."""
    with pytest.raises(TypeError, match="bool"):
        parse_decimal(True)
    with pytest.raises(TypeError, match="bool"):
        parse_decimal(False)


def test_parse_decimal_rejects_other_types() -> None:
    with pytest.raises(TypeError):
        parse_decimal([1, 2])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        parse_decimal({"a": 1})  # type: ignore[arg-type]
