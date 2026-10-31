"""Tests de l'errata OpenAPI Pennylane (v0.2 A.4).

Cf. cahier des charges `a0b9a49` §3.A.4 + `docs/errata_openapi_pennylane.md`.

Ces tests caractérisent **factuellement** les duplications `oneOf` détectées
dans la spec OpenAPI Pennylane V2 brute (`openapi_spec_raw.json`, non
versionnée — régénérée par `scripts/regenerate_openapi.sh`). Si Pennylane
corrige la spec en amont, ces tests **échoueront** : c'est un signal de
remédiation amont qui doit déclencher la revue de l'errata + suppression
du contournement de filtrage.

Skip si spec brute absente (cas CI sans téléchargement préalable). Les
mêmes signatures sont absentes de `openapi_spec_filtered.json` (versionnée,
filtrée v0.1 — zones customer_invoices/quotes/etc. élidées). C'est attendu :
le filtre est le contournement actuel.

Sourçage initial : capture empirique 2026-05-12 sur `openapi_spec_raw.json`
(commit `1be1921` régénéré depuis Pennylane V2). Voir
`docs/errata_openapi_pennylane.md` pour détail patterns + zones.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from collections import defaultdict

import pytest

_REPO_RACINE = pathlib.Path(__file__).resolve().parent.parent
SPEC_RAW_PATH = _REPO_RACINE / "openapi_spec_raw.json"
SPEC_FILTERED_PATH = _REPO_RACINE / "openapi_spec_filtered.json"


# Capture empirique 2026-05-12 (commit cahier A.4). Chaque entrée :
# (hash signature md5[:8], n_occurrences_attendues, étiquette descriptive).
EXPECTED_DUPLICATED_PATTERNS = [
    ("cf03001b", 11, "currency_nullable_enum_iso_or_null"),
    ("d362a6d3", 5, "billing_payment_conditions_enum_or_null"),
    ("daa153c7", 4, "customer_invoices_lines_draft_oneof"),
    ("c404cd0d", 4, "quotes_invoice_lines_oneof"),
    ("cb612a6d", 3, "customers_response_with_or_without_categories"),
    ("c2efe0b7", 2, "customer_invoices_lines_finalized_oneof"),
    ("2625c7fc", 2, "customer_invoices_discount_value_nullable"),
]
TOTAL_ONEOF_EXPECTED = 34
TOTAL_SIGNATURES_EXPECTED = 10


def _walk_oneof(node: object, path: str = "") -> list[tuple[str, str]]:
    """Walk récursif. Retourne [(path, signature_md5_short), ...] pour chaque oneOf."""
    hits: list[tuple[str, str]] = []
    if isinstance(node, dict):
        if "oneOf" in node and isinstance(node["oneOf"], list):
            sig = json.dumps(node["oneOf"], sort_keys=True)
            h = hashlib.md5(sig.encode()).hexdigest()[:8]
            hits.append((path, h))
        for k, v in node.items():
            sub_path = f"{path}.{k}" if path else k
            hits.extend(_walk_oneof(v, sub_path))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            hits.extend(_walk_oneof(item, f"{path}[{i}]"))
    return hits


_skip_no_raw = pytest.mark.skipif(
    not SPEC_RAW_PATH.exists(),
    reason=(
        "openapi_spec_raw.json absent — régénérer via "
        "scripts/regenerate_openapi.sh pour exécuter ces tests."
    ),
)


@pytest.fixture(scope="module")
def raw_spec() -> dict:
    """Charge `openapi_spec_raw.json` une fois par module."""
    with SPEC_RAW_PATH.open() as f:
        return json.load(f)


@pytest.fixture(scope="module")
def raw_hits(raw_spec: dict) -> list[tuple[str, str]]:
    return _walk_oneof(raw_spec)


@pytest.fixture(scope="module")
def raw_by_hash(raw_hits: list[tuple[str, str]]) -> dict[str, list[str]]:
    by_hash: dict[str, list[str]] = defaultdict(list)
    for path, h in raw_hits:
        by_hash[h].append(path)
    return dict(by_hash)


# ─── Caractérisation globale ─────────────────────────────────────────────────


@_skip_no_raw
def test_raw_spec_total_oneof_occurrences(raw_hits: list[tuple[str, str]]) -> None:
    """Total `oneOf` capturés en 2026-05-12. Sentinel pour évolutions amont."""
    assert len(raw_hits) == TOTAL_ONEOF_EXPECTED, (
        f"Total oneOf {len(raw_hits)} ≠ {TOTAL_ONEOF_EXPECTED} attendu. "
        "Pennylane a peut-être corrigé/ajouté des oneOf — mettre à jour l'errata."
    )


@_skip_no_raw
def test_raw_spec_distinct_signatures(raw_by_hash: dict[str, list[str]]) -> None:
    """10 signatures distinctes attendues (3 uniques + 7 dupliquées)."""
    assert len(raw_by_hash) == TOTAL_SIGNATURES_EXPECTED, (
        f"{len(raw_by_hash)} signatures distinctes ≠ {TOTAL_SIGNATURES_EXPECTED} "
        "attendues. Évolution amont à investiguer."
    )


@_skip_no_raw
def test_raw_spec_duplicated_patterns_count(
    raw_by_hash: dict[str, list[str]],
) -> None:
    """7 patterns dupliqués attendus (occurrences > 1)."""
    duplicated = {h: paths for h, paths in raw_by_hash.items() if len(paths) > 1}
    assert len(duplicated) == len(EXPECTED_DUPLICATED_PATTERNS), (
        f"{len(duplicated)} patterns dupliqués ≠ {len(EXPECTED_DUPLICATED_PATTERNS)} attendus."
    )


# ─── Caractérisation pattern-par-pattern ──────────────────────────────────────


@_skip_no_raw
@pytest.mark.parametrize(
    "expected_hash,expected_count,label",
    EXPECTED_DUPLICATED_PATTERNS,
    ids=[label for _, _, label in EXPECTED_DUPLICATED_PATTERNS],
)
def test_raw_spec_pattern_occurrences(
    raw_by_hash: dict[str, list[str]],
    expected_hash: str,
    expected_count: int,
    label: str,
) -> None:
    """Pour chaque pattern dupliqué identifié, asserte le nombre d'occurrences.

    Si l'occurrence varie : Pennylane a peut-être consolidé ce schéma. C'est
    un signal positif — mettre à jour l'errata pour refléter la correction
    amont, et envisager d'élargir le filter_spec.py pour ne plus l'exclure.
    """
    paths = raw_by_hash.get(expected_hash, [])
    assert len(paths) == expected_count, (
        f"Pattern {expected_hash} ({label}) : {len(paths)} occurrences "
        f"≠ {expected_count} attendues. "
        f"Sample paths: {paths[:3]}"
    )


# ─── Sentinel filtre v0.1 — la spec filtrée doit rester « propre » ────────────


def test_filtered_spec_has_no_oneof() -> None:
    """`openapi_spec_filtered.json` (versionnée v0.1) ne doit contenir aucun
    `oneOf` : le filtrage v0.1 a élidé les zones affectées (customer_invoices,
    quotes, billing_subscriptions, customers, transactions paginées).

    Si ce test échoue, le filtre `scripts/filter_spec.py` doit être revu
    avant régénération `_generated/` (génération `openapi-python-client`
    bloquerait sur les oneOf dupliqués).
    """
    if not SPEC_FILTERED_PATH.exists():
        pytest.skip("openapi_spec_filtered.json absent du repo.")
    with SPEC_FILTERED_PATH.open() as f:
        spec = json.load(f)
    hits = _walk_oneof(spec)
    assert hits == [], (
        f"{len(hits)} oneOf trouvés dans spec filtrée v0.1 — le filtre "
        "n'élimine plus les zones affectées. Vérifier filter_spec.py "
        "avant régénération _generated/."
    )
