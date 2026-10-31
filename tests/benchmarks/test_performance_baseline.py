"""Benchmarks performance baseline `grandlivre` v0.3.

Cs B.4 Mission durcissement #3 audit sécurité — séquence durcissement
v0.2 stabilisation. Baseline reproductible pour détection régression
performance >20 % cumul session (cf. README.md seuil).

3 benchmarks sync ciblés couvrant chemin critique parsing + instantiation :

1. `parse_decimal` canonical (parsing strict montants Pennylane)
2. `parse_decimal` passthrough Decimal (fast path no-op)
3. `PennylaneClient.__init__` (instantiation + headers build)

Doctrine §2.15 simplicity criterion : benchmarks sync simples
reproductibles privilégiés. Benchmarks async resources (TrialBalance,
LedgerEntries, SupplierInvoices) reportés itération future v2 — respx
mock setup intra-benchmark loop nécessite refactor signature mocks
+ pytest-benchmark async plugin compat (effort > §2.18 critère
succès initial). Cohérent §2.5 no-debt (pas stub, scope limité strict
à ce qui fonctionne empiriquement).
"""

from __future__ import annotations

from decimal import Decimal

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.parsing import parse_decimal


def test_benchmark_parse_decimal_canonical(benchmark) -> None:
    """Benchmark parse_decimal sur input string standard montant."""
    result = benchmark(parse_decimal, "1200.50")
    assert result == Decimal("1200.50")


def test_benchmark_parse_decimal_passthrough(benchmark) -> None:
    """Benchmark parse_decimal fast path Decimal already."""
    d = Decimal("3.14")
    result = benchmark(parse_decimal, d)
    assert result is d


def test_benchmark_client_init(benchmark, settings: Settings) -> None:
    """Benchmark PennylaneClient instantiation + headers build."""
    client = benchmark(PennylaneClient, settings=settings)
    assert client is not None
