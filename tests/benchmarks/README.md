# Benchmarks performance `grandlivre`

Baseline performance reproductible pour détection régression continue.

## Périmètre

3 benchmarks `pytest-benchmark` couvrant chemin critique sync de la
bibliothèque :

| Benchmark | Cible | Mean baseline initial | OPS |
|---|---|---|---|
| `parse_decimal_passthrough` | Fast path `Decimal` already | ~150 ns | 6.67M ops/s |
| `parse_decimal_canonical` | Parsing strict montants `str → Decimal` | ~785 ns | 1.27M ops/s |
| `client_init` | Instantiation `PennylaneClient` + headers build | ~10 ms | ~100 ops/s |

Baseline empirique : `tests/benchmarks/baseline.json` (tracked, stats-only ~5 KB).

## Exécution locale

```bash
pip install -e ".[test,dev]"
pytest tests/benchmarks/ --benchmark-only
```

Régénérer baseline si évolution structurelle attendue :

```bash
pytest tests/benchmarks/ --benchmark-only \
  --benchmark-json=tests/benchmarks/baseline.json
```

Puis strip aux stats minimums (script Python intégré commit message ou
exécution manuelle conservant uniquement `machine_info` + `commit_info` +
`datetime` + `version` + `benchmarks[].{name, fullname, group, stats}`).

## Seuil régression

**Critère fail CI** : `mean` >20 % vs baseline pour un benchmark donné.

Justification 20 % : tolérance variabilité environnement CI (Linux runners
shared, GitHub Actions notamment) tout en détectant régression structurelle
réelle (algorithme moins efficient, dépendance lente nouvelle, etc.).

## Périmètre exclu

Benchmarks async resources (`TrialBalanceResource.get`,
`LedgerEntriesResource.paginate`, `SupplierInvoicesResource.paginate`)
reportés itération future v2 — respx mock setup intra-benchmark loop
nécessite refactor signatures mocks + `pytest-benchmark` async plugin
compat. Couverture actuelle suffisante pour baseline reproductible v0.2
chemin critique parsing + instantiation (cohérent doctrine no-debt,
scope strict §2.14 atomicité).

## Itération future v2 (si justifié empirique)

- Benchmarks async `respx`-mocked end-to-end resources (TrialBalance,
  LedgerEntries, SupplierInvoices, Customers, Suppliers, etc.)
- Benchmarks rate limiting `aiolimiter` throughput burst vs sustained
- Benchmarks retry tenacity backoff cumul exponential
- Benchmark redaction `Authorization` headers exception construction

À planifier si régression performance observée OR évolution majeure lib
nécessitant validation throughput cibles SLA.

## Référence

- Cs B.4 Mission durcissement #3 audit sécurité (cf. `CHANGELOG.md`)
- `pytest-benchmark` documentation : <https://pytest-benchmark.readthedocs.io/>
- Configuration `pyproject.toml [project.optional-dependencies] dev`
  ajoute `pytest-benchmark>=4` (Cs B.1)
