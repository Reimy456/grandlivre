"""Filtre la spec OpenAPI Pennylane brute pour ne conserver que les paths v0.1.

Référence : plan harmonic-hatching-trinket.md Phase 2.

Lecture : spec brute téléchargée par regenerate_openapi.sh.
Écriture : spec filtrée (sous-ensemble GET pour les 3 endpoints + endpoints
de résolution interne fiscal_years, ledger_accounts, journals).

Résolution récursive des $ref : tous les schémas transitivement référencés par
les paths conservés sont préservés dans components.schemas.

Justification d'inclure /journals : exposé en lecture pour le contexte
(filtre journal_id sur ledger_entries). /ledger_accounts pour résolution
account_number → ledger_account_id. /fiscal_years pour résolution
exercice "YYYY" → période.

Usage :
    python filter_spec.py <input_full.json> <output_filtered.json>
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Paths à conserver — regex anchored, méthodes GET uniquement.
# Le préfixe /api/external/v2/ est conservé tel que servi par l'API.
KEEP_PATH_PATTERNS = [
    re.compile(r"^/api/external/v2/trial_balance$"),
    re.compile(r"^/api/external/v2/ledger_entry_lines$"),
    re.compile(r"^/api/external/v2/ledger_entry_lines/\{id\}$"),
    re.compile(r"^/api/external/v2/ledger_accounts$"),
    re.compile(r"^/api/external/v2/ledger_accounts/\{id\}$"),
    re.compile(r"^/api/external/v2/fiscal_years$"),
    re.compile(r"^/api/external/v2/journals$"),
    re.compile(r"^/api/external/v2/journals/\{id\}$"),
    re.compile(r"^/api/external/v2/supplier_invoices$"),
    re.compile(r"^/api/external/v2/supplier_invoices/\{id\}$"),
]

KEEP_METHODS = {"get"}


def path_matches(path: str) -> bool:
    return any(pat.match(path) for pat in KEEP_PATH_PATTERNS)


def collect_refs(node: Any, out: set[str]) -> None:
    """Walks node and collects all $ref values."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                out.add(v)
            else:
                collect_refs(v, out)
    elif isinstance(node, list):
        for item in node:
            collect_refs(item, out)


def resolve_schema_name(ref: str) -> str | None:
    """`#/components/schemas/Foo` → `Foo`. Returns None if not a schema ref."""
    prefix = "#/components/schemas/"
    if ref.startswith(prefix):
        return ref[len(prefix):]
    return None


def transitively_collect_schemas(
    initial_refs: Iterable[str], all_schemas: dict[str, Any]
) -> set[str]:
    """BFS sur les $ref pour collecter tous les schémas transitivement référencés."""
    queue: list[str] = []
    for ref in initial_refs:
        name = resolve_schema_name(ref)
        if name is not None and name in all_schemas:
            queue.append(name)

    seen: set[str] = set()
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)

        schema = all_schemas.get(name)
        if schema is None:
            continue

        child_refs: set[str] = set()
        collect_refs(schema, child_refs)
        for child_ref in child_refs:
            child_name = resolve_schema_name(child_ref)
            if child_name is not None and child_name in all_schemas and child_name not in seen:
                queue.append(child_name)

    return seen


def filter_spec(spec: dict[str, Any]) -> dict[str, Any]:
    all_paths: dict[str, Any] = spec.get("paths", {})
    all_schemas: dict[str, Any] = spec.get("components", {}).get("schemas", {})

    # 1. Conserver les paths matchant + méthodes GET uniquement.
    filtered_paths: dict[str, Any] = {}
    for path, operations in all_paths.items():
        if not path_matches(path):
            continue
        kept_ops: dict[str, Any] = {}
        for method, op in operations.items():
            if method.lower() in KEEP_METHODS:
                kept_ops[method] = op
            elif method.lower() in ("parameters", "summary", "description"):
                kept_ops[method] = op
        if kept_ops:
            filtered_paths[path] = kept_ops

    # 2. Collecter les $ref des paths conservés.
    initial_refs: set[str] = set()
    collect_refs(filtered_paths, initial_refs)

    # 3. Collecter aussi les $ref des parameters globaux si présents.
    global_params = spec.get("components", {}).get("parameters", {})
    if global_params:
        collect_refs(global_params, initial_refs)

    # 4. BFS transitif sur les schémas.
    kept_schema_names = transitively_collect_schemas(initial_refs, all_schemas)

    # 5. Construire le spec filtré.
    out: dict[str, Any] = {
        "openapi": spec.get("openapi"),
        "info": spec.get("info"),
        "servers": spec.get("servers"),
        "paths": filtered_paths,
        "components": {},
    }

    out["components"]["schemas"] = {
        name: all_schemas[name] for name in sorted(kept_schema_names)
    }

    # Préserve aussi parameters globaux, securitySchemes si présents.
    src_components = spec.get("components", {})
    for key in ("parameters", "securitySchemes", "responses", "requestBodies"):
        if key in src_components:
            out["components"][key] = src_components[key]

    return out


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "Usage: python filter_spec.py <input_full.json> <output_filtered.json>",
            file=sys.stderr,
        )
        return 2

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    if not input_path.exists():
        print(f"Input not found: {input_path}", file=sys.stderr)
        return 1

    spec = json.loads(input_path.read_text(encoding="utf-8"))
    filtered = filter_spec(spec)
    output_path.write_text(
        json.dumps(filtered, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    paths_count = len(filtered["paths"])
    schemas_count = len(filtered["components"]["schemas"])
    out_size = output_path.stat().st_size

    print(f"Filtered spec: {paths_count} paths, {schemas_count} schemas, {out_size:,} bytes")
    print(f"Wrote: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
