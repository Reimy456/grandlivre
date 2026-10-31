# Sentinelle OpenAPI spec drift Pennylane V2

Détection régression structurelle entre spec OpenAPI Pennylane V2 (`openapi_spec_raw.json` tracked baseline) et modèles Pydantic v2 `grandlivre.models.*`.

## Mode 1 — pre-commit local (livré v0.2)

`tests/sentinelle/test_openapi_spec_drift.py` compare la baseline tracked
`openapi_spec_raw.json` (fetché empiriquement 2026-05-11, 120 paths +
115 schemas) à l'introspection Pydantic des 46 modèles exportés.

9 sentinelles couvrent :

- **Structure baseline** (4 tests) : JSON loads valid + OpenAPI 3.x + 120
  paths baseline + 115 schemas baseline.
- **Modèles Pydantic core** (5 tests) : `MeUser`, `FiscalYear`, `Journal`,
  `LedgerAccount`, `TrialBalanceAccount` conservent leurs champs critiques.

Exécution :

```bash
pytest tests/sentinelle/
```

Fail si :

- `openapi_spec_raw.json` corrompu, structure cassée, ou seuils baseline
  (120 paths / 115 schemas) dépassés en moins (régression).
- Modèles Pydantic perdent leurs champs core (ex. `MeUser.email` retiré).

## Mode 2 — CI nightly opt-in (différé itération future)

Workflow GitHub Actions séparé (non livré v0.2) :

```yaml
name: sentinelle-openapi-drift-nightly
on:
  schedule:
    - cron: '0 3 * * *'  # 03:00 UTC chaque jour
jobs:
  drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Refetch OpenAPI spec
        run: bash scripts/regenerate_openapi.sh
      - name: Diff vs baseline tracked
        run: |
          if ! diff -q openapi_spec_raw.json baseline_openapi_spec.json; then
            echo "::warning::OpenAPI spec drift detected — investigate"
            exit 1
          fi
```

Détecte drift amont API Pennylane (nouveau path ajouté, champ obligatoire
ajouté, type changement). Nécessite Company Token + accès réseau —
incompatible doctrine `respx zero-network obligatoire` (cf.
`CONTRIBUTING.md:88-92`). Implémentation future séparée si justifié
empirique.

## Procédure update modèles si drift détecté

1. Régénérer spec : `bash scripts/regenerate_openapi.sh` (nécessite Company
   Token + accès API V2).
2. Inspecter diff `git diff openapi_spec_raw.json`.
3. Si nouveau champ obligatoire spec :
   - Ajouter champ à modèle Pydantic concerné `grandlivre/models/<X>.py`
   - Mettre à jour fixtures tests respx si nécessaire
   - Mettre à jour seuils baseline `BASELINE_PATHS_COUNT` / `BASELINE_SCHEMAS_COUNT`
     dans `tests/sentinelle/test_openapi_spec_drift.py` si applicable.
4. Si champ deprecated/retiré spec : marquer Pydantic field optional ou
   retirer (breaking change v2.0.0 cf. `CONTRIBUTING.md`).
5. Re-run `pytest tests/sentinelle/` jusqu'à 9/9 pass.

## Référence

- Cs B.5 Mission durcissement #3 audit sécurité (cf. `CHANGELOG.md`)
- Baseline tracked : `openapi_spec_raw.json` (1.9 MB)
- Script regen : `scripts/regenerate_openapi.sh`
- Modèles Pydantic : `src/grandlivre/models/`
- Cohérent design choice README.md « Single source of truth OpenAPI »
