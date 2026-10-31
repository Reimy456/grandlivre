# Tests d'intégration sandbox réelle

Tests marqués `@pytest.mark.smoke`. **Exclus du `pytest` par défaut** (voir
`pyproject.toml :: tool.pytest.ini_options :: addopts = "-m 'not smoke'"`).

## Prérequis

- Variable d'environnement `PENNYLANE_API_TOKEN` configurée (ou présente dans
  `.env` à la racine du projet).
- Connectivité Internet vers `app.pennylane.com`.

## Exécution

```bash
# Depuis la racine du projet
source venv/bin/activate
pytest -m smoke tests/smoke/ -v
```

## Périmètre

- `test_smoke_fiscal_years_available` — récupère les exercices fiscaux
  disponibles ; assure que l'auth fonctionne et qu'au moins un exercice est
  exposé.
- `test_smoke_trial_balance_first_exercice` — appelle `TrialBalanceResource`
  sur le premier exercice trouvé.
- `test_smoke_ledger_entries_first_exercice` — appelle `LedgerEntriesResource`
  avec cap=10, sans filtrage par compte.
- `test_smoke_supplier_invoices_minimal` — appelle `SupplierInvoicesResource`
  avec cap=10, sans filtre.
- `test_smoke_capture_ledger_schema` — capture le JSON brut d'une réponse
  `/ledger_entry_lines` (anonymisée a posteriori si nécessaire) dans
  `fixtures/` pour servir de fixture future et résoudre la dette P1 Module A.2 (schéma empirique vérifié).

## Confidentialité

Les fixtures capturées dans `fixtures/` peuvent contenir des données métier du
dossier de test. **Vérifier avant tout commit** que les données sont des données
de démonstration sans secret réel. Le `.gitignore` interdit la capture
accidentelle du token via les exceptions (Discipline R-code-2).
