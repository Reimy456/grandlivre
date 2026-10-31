# Changelog — grandlivre

Format [Keep a Changelog](https://keepachangelog.com/), versionnement
[SemVer 2.0](https://semver.org/). Le dépôt public démarre à la 1.0.0 : l'historique
de développement antérieur n'est pas repris.

## [Unreleased]

## [1.0.0] — 2026-10-30

Première version publiée.

### Added

- Client async `PennylaneClient` (httpx) : rate limiting par `cabinet_id`
  (`aiolimiter`, un limiteur par boucle d'événements), retry `tenacity` sur 429/5xx,
  exceptions typées avec masquage des en-têtes sensibles et indication de réparation
  (`hint`), token `SecretStr` (`pydantic-settings`).
- 18 ressources en lecture : `trial_balance` (balance générale ; `is_auxiliary=True`
  rend les comptes par tiers), `ledger_entries` (lignes, avec `lettered_line_ids`, et
  écritures regroupées), `journals`, `ledger_accounts`, `fiscal_years`, `customers`,
  `suppliers`, `supplier_invoices` (avec `invoice_lines` et l'écriture liée),
  `customer_invoices`, `bank_accounts`, `transactions` (filtres par période et par
  compte bancaire, `matched_invoices`), `lettering`, `fec`, `general_ledgers`,
  `analytical_general_ledgers` (création de job, suivi, téléchargement avec suivi de
  la redirection), `me`, `changelogs/*`. Pagination par curseur avec cap dur,
  itérateurs asynchrones, montants en `Decimal` ; pages de 1 000 pour le plan de
  comptes et la balance.
- Filtres des listes dans le paramètre `filter` JSON de l'API (module `filtre`) :
  période, tiers, `payment_status` (factures fournisseurs), `draft` et `credit_note`
  (factures clients), `invoice_number` exact, compte bancaire. Un paramètre en clair
  est ignoré en silence par l'API : la bibliothèque n'en envoie aucun.
- `resolve_fiscal_year(client, exercice)` : bornes d'un exercice à partir de son année.
- Couche d'écriture anti-doublon (WAL) : `LedgerEntriesResource.create`,
  `InvoicePaymentsResource.update_payment_status`, `.mark_customer_invoice_paid`,
  `.match_payment`, `CustomerInvoicesResource.import_invoice` (pièce PDF puis import),
  `.import_e_invoice` (facture électronique Factur-X, traitement différé chez
  Pennylane), `.link_credit_note` (rattachement d'un avoir). États
  `PENDING → IN_FLIGHT → CONFIRMED | FAILED | IN_DOUBT`, empreinte SHA-256 du payload
  (et du PDF) comme clé d'idempotence, `recover()` après crash, `WALManager.resolve`
  pour la réconciliation humaine.
- Stores d'idempotence : `SqliteIdempotencyStore` (recommandé), `JsonlStore`,
  `NullStore` (lecture seule / tests), protocole `IdempotencyStore` et contrat de
  tests partagé `store_testing` (y compris l'identité d'un write sans identifiant
  rendu, cas des réponses `204`) ; `ensure_deployment_safety`.
- Court-circuit vérifié : existence de l'objet mémorisé revérifiée au rejeu d'une
  création ou d'un import (suppression côté Pennylane détectée, rejeu unique) ; état
  courant de la facture relu avant de répondre « déjà fait » sur les changements de
  statut de paiement (renvoi unique si l'état a changé entre-temps, aucun envoi si
  l'état est ambigu ; `InvoiceWriteAck.observed_state` et `state_verified`).
- Sentinelle de dérive de la spec OpenAPI (`tests/sentinelle/`) et tests d'errata
  (`tests/test_openapi_errata.py`) sur la spec brute versionnée.
- Tests d'intégration optionnels contre un dossier réel (`pytest -m smoke`).
- CI GitHub Actions : tests Python 3.12/3.13 avec seuil de couverture, `ruff`,
  `mypy --strict`, `pip-audit` ; publication PyPI par *trusted publishing* sur tag.

### Notes

- `SupplierInvoice.deadline` porte l'échéance (le champ `due_date` d'une version de
  développement antérieure n'existait pas chez l'API).
- `LedgerEntryLine` suit le schéma 2026 de `/ledger_entry_lines` : `date` et
  `journal` au niveau de la ligne, `ledger_entry = {id}` ; les en-têtes d'écriture
  viennent de `LedgerEntriesGroupedResource`.
- `openapi_spec_raw.json` est le document OpenAPI publié par Pennylane, reproduit
  tel quel pour les tests (voir `NOTICE`) ; `scripts/regenerate_openapi.sh` le
  rafraîchit depuis la source.
