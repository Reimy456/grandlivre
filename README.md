# grandlivre

> **Unofficial Python client for the Pennylane accounting API (v2).** Async, typed,
> read *and* idempotent write, validated against the real API. Not affiliated with
> Pennylane. *English summary: [README.en.md](README.en.md).*

Bibliothèque Python **non officielle** pour l'API comptabilité Pennylane V2
(`https://app.pennylane.com/api/external/v2`) : lecture des données comptables
d'un dossier, et écriture (écritures, statut de paiement, rapprochement, import de
factures et d'avoirs électroniques) avec garantie anti-doublon.

**Pour qui** : les développeurs qui automatisent la comptabilité d'entreprises tenues
sur Pennylane (scripts, back-office, agents). **Ce que c'est** : un client HTTP typé
et asynchrone, indépendant de tout modèle de langage ; il s'utilise dans un script, un
service, ou derrière un serveur MCP appelé par Claude, Mistral ou tout autre client.
**Ce que ce n'est pas** : ni un logiciel comptable, ni un SDK officiel, ni un outil
d'IA. Pennylane propose depuis août 2026 un serveur MCP officiel, en lecture seule ;
cette bibliothèque reste le bon outil pour un script, un service, et pour l'écriture
idempotente.

[![CI](https://github.com/Reimy456/grandlivre/actions/workflows/test.yml/badge.svg)](https://github.com/Reimy456/grandlivre/actions/workflows/test.yml)
[![Lint](https://github.com/Reimy456/grandlivre/actions/workflows/lint.yml/badge.svg)](https://github.com/Reimy456/grandlivre/actions/workflows/lint.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-%3E%3D3.12-blue.svg)](https://www.python.org/)

> **Marque et périmètre.** « Pennylane » est une marque de Pennylane SAS ; ce projet
> n'est ni développé, ni soutenu, ni approuvé par Pennylane. Le nom est utilisé
> uniquement pour désigner le service auquel la bibliothèque se connecte. Aucun
> rapport non plus avec [PennyLane](https://pennylane.ai/), la bibliothèque de calcul
> quantique de Xanadu (homonyme).

## Statut

**1.0.0 — première version stable.** La surface publique suit SemVer : une rupture
d'interface donne une version majeure, le CHANGELOG dit chaque changement.

**Lecture — 18 ressources** :

- comptabilité : `trial_balance` (balance générale, et balance auxiliaire avec les
  comptes par tiers), `ledger_entries` (lignes plates, avec leur état de lettrage, et
  écritures regroupées), `journals`, `ledger_accounts`, `fiscal_years` ;
- tiers : `customers`, `suppliers`, `supplier_invoices` (avec les lignes de facture
  et l'écriture liée), `customer_invoices` ;
- trésorerie : `bank_accounts`, `transactions` (filtres par compte et par période,
  factures rattachées), `lettering` (lecture) ;
- exports réglementaires : `fec`, `general_ledgers`, `analytical_general_ledgers`
  (création de job, suivi, téléchargement) ;
- référentiel : `me`, `changelogs/*`.

Les filtres des listes partent dans le paramètre `filter` de l'API (JSON), le seul
que l'API applique : un paramètre en clair y est ignoré en silence, la bibliothèque
ne les emploie plus.

**Écriture — 7 opérations** adossées à un journal d'écriture (WAL) anti-doublon :

- `LedgerEntriesResource.create` — création d'écriture (`POST /ledger_entries`) ;
- `InvoicePaymentsResource.update_payment_status` — statut de paiement d'une
  facture fournisseur ;
- `InvoicePaymentsResource.mark_customer_invoice_paid` — facture client payée ;
- `InvoicePaymentsResource.match_payment` — rapprochement d'une transaction ;
- `CustomerInvoicesResource.import_invoice` — import d'une facture client avec sa
  pièce PDF (`POST /file_attachments` puis `POST /customer_invoices/import`) ;
- `CustomerInvoicesResource.import_e_invoice` — import d'une facture électronique
  Factur-X (`POST /customer_invoices/e_invoices/imports`, traitement différé chez
  Pennylane) ;
- `CustomerInvoicesResource.link_credit_note` — rattachement d'un avoir à sa facture.

**Tests** : tests unitaires `respx` (zéro appel réseau) exécutés en CI sur Python
3.12 et 3.13 avec seuil de couverture, `mypy --strict`, `ruff`, `pip-audit` ; tests
d'intégration optionnels contre un dossier réel (`pytest -m smoke`).

### Validation contre l'API réelle

| Périmètre | État |
|---|---|
| Lecture : balance (générale et auxiliaire), lignes et écritures, journaux, plan de comptes, exercices, `me`, factures fournisseurs et clients, tiers, lettrage, changelogs, transactions, comptes bancaires | Validé sur dossier réel (tests smoke et campagnes de mai à octobre 2026) |
| Filtres des listes (`filter` JSON : période, tiers, statut de paiement, numéro de facture, compte bancaire) | Validé sur dossier réel en octobre 2026 : un filtre en clair est ignoré par l'API, le filtre JSON est appliqué |
| Exports FEC / grands livres / grands livres analytiques | Création de job et suivi validés en réel (septembre 2026) ; le lien de téléchargement redirige vers un stockage objet (constaté en réel), la bibliothèque suit la redirection, couvert par un test sans réseau |
| Écriture : `create` | Validé en réel (juin 2026), y compris les cas de suppression côté Pennylane après confirmation |
| Écriture : `update_payment_status`, `mark_customer_invoice_paid`, `match_payment` | Validés en réel (août 2026), y compris « statut modifié dans Pennylane entre deux demandes » |
| Écriture : `import_invoice` | Validé en réel (octobre 2026) : import, rejeu idempotent, pièce rattachée |
| Écriture : `import_e_invoice`, `link_credit_note` | Validés en réel (octobre 2026) : avoir Factur-X importé, rattaché, facture d'origine partiellement annulée |

À noter, établi empiriquement car la spec ne le documente pas : côté fournisseur,
`PUT paid` donne `payment_status = "paid_offline"` et `PUT to_be_paid` donne
`"to_be_paid"` ; un rapprochement bancaire complet laisse `payment_status` inchangé
mais passe `paid` à `true` et le reste à payer à `0`. Deux notions distinctes, que la
sonde d'état de la bibliothèque traite toutes les deux. L'échéance d'une facture
fournisseur se lit sous `deadline`, comme pour les factures clients.

## Installation

```bash
pip install grandlivre
```

Python ≥ 3.12. Dépendances : `httpx`, `pydantic` (v2), `pydantic-settings`,
`aiolimiter`, `tenacity`, `anyio`.

## Lecture — démarrage rapide

```python
import asyncio
from grandlivre import (
    LedgerEntriesResource,
    PennylaneClient,
    Settings,
    SupplierInvoicesResource,
    TrialBalanceResource,
    resolve_fiscal_year,
)


async def main() -> None:
    settings = Settings()  # lit PENNYLANE_API_TOKEN depuis l'environnement ou .env
    async with PennylaneClient(settings=settings) as client:
        # Balance d'un exercice ; is_auxiliary=True rend les comptes par tiers.
        tb = await TrialBalanceResource(client).get(exercice="2025", is_auxiliary=True)
        for account in tb.accounts:
            print(account.number, account.label, account.balance)

        # Lignes d'écritures d'un compte (pagination par curseur, cap dur) ;
        # chaque ligne porte les identifiants des lignes lettrées avec elle.
        page = await LedgerEntriesResource(client).paginate(
            exercice="2025", account_number="411100241", cap=500
        )
        for line in page.lines:
            print(line.entry_date, line.line_label, line.debit, line.credit, line.lettered_line_ids)

        # Factures fournisseurs sur la période d'un exercice (filtre JSON côté API).
        fy = await resolve_fiscal_year(client, exercice="2025")
        invoices = await SupplierInvoicesResource(client).paginate(
            date_from=fy.start, date_to=fy.finish, cap=500
        )
        for inv in invoices.invoices:
            print(inv.id, inv.currency_amount, inv.date, inv.deadline)


asyncio.run(main())
```

Chaque ressource expose selon le cas `list(...)`, `paginate(...)` (page + curseur de
continuation + cap dur), `iterate(...)` (itérateur asynchrone) et `get(id)`. Les
montants sont des `decimal.Decimal` : un `float` en entrée est rejeté.

## Écriture — démarrage rapide

```python
from pathlib import Path
from grandlivre import (
    InvoicePaymentsResource,
    LedgerEntriesResource,
    NewLedgerEntryLine,
    PennylaneClient,
    Settings,
    SqliteIdempotencyStore,
)

store = SqliteIdempotencyStore(Path("~/.grandlivre/wal.sqlite").expanduser())

async with PennylaneClient(settings=Settings()) as client:
    entry = await LedgerEntriesResource(client, wal_store=store).create(
        date="2026-03-31",
        label="Assurance RC — régularisation",
        journal_id=123,
        lines=[
            NewLedgerEntryLine(ledger_account_id=1001, debit="120.00"),
            NewLedgerEntryLine(ledger_account_id=2002, credit="120.00"),
        ],
        namespace="dossier-42",   # clé de tenancy : un WAL logique par dossier
    )
    print(entry.id)

    ack = await InvoicePaymentsResource(client, wal_store=store).update_payment_status(
        invoice_id=4905972085, payment_status="paid", namespace="dossier-42"
    )
    print(ack.status, ack.state_verified, ack.observed_state, ack.detail)
```

### Ce que garantit la couche d'écriture

L'API Pennylane V2 n'offre aucune clé d'idempotence : renvoyer deux fois la même
création produit deux écritures. La bibliothèque apporte la protection côté client :

- **Journal d'écriture (WAL) avant tout envoi.** Chaque opération est enregistrée
  `IN_FLIGHT` sur disque *avant* la requête, puis `CONFIRMED`, `FAILED` ou
  `IN_DOUBT`. L'empreinte SHA-256 du payload canonique (et du PDF pour un import)
  sert de clé : renvoyer la même opération court-circuite (zéro requête) au lieu de
  dupliquer.
- **Jamais de renvoi automatique dans le doute.** Timeout, 5xx ou réponse
  illisible après envoi → `IN_DOUBT` et `WALInDoubtError` : un humain tranche via
  `grandlivre.wal.WALManager.resolve`, après vérification chez Pennylane.
  `recover()` liste ce qui est resté en vol après un crash.
- **Court-circuit vérifié.** Pour la création et les imports, l'existence de l'objet
  mémorisé est revérifiée (une suppression côté Pennylane est détectée, l'opération
  est rejouée une fois). Pour les statuts de paiement, l'état courant de la facture
  est relu et confronté au statut demandé : conforme → « déjà fait » vérifié ;
  contraire → renvoi unique ; ambigu (paiement en cours, partiel…) → aucun envoi,
  `state_verified=False`.
- **Stores.** `SqliteIdempotencyStore` (recommandé, atomicité native), `JsonlStore`
  (mono-machine, hérité), ou votre implémentation du protocole `IdempotencyStore`
  (par exemple Postgres pour plusieurs machines), validée par le contrat de tests
  partagé `grandlivre.store_testing`. `ensure_deployment_safety` refuse un
  déploiement multi-écrivains sur un store qui ne le supporte pas.

Ce que cela **ne** garantit pas : une sémantique « exactement une fois » de bout en
bout. La garantie est « au plus un envoi par opération, et jamais de doublon
silencieux » ; les cas incertains remontent à un humain.

## Configuration

Variables d'environnement (préfixe `PENNYLANE_`), lues aussi depuis un fichier
`.env` du répertoire courant :

| Variable | Défaut | Description |
|---|---|---|
| `PENNYLANE_API_TOKEN` | (requis) | Company Token Pennylane (Bearer). |
| `PENNYLANE_BASE_URL` | `https://app.pennylane.com/api/external/v2` | URL de base de l'API V2. |
| `PENNYLANE_RATE_LIMIT` | `5` | Plafond requêtes/seconde (5 = Company Token ; 4 = Firm Token). |
| `PENNYLANE_CABINET_ID` | `default` | Clé du limiteur de débit (multi-tenant). |
| `PENNYLANE_REQUEST_TIMEOUT_SECONDS` | `30` | Timeout HTTP par requête. |
| `PENNYLANE_USE_2026_API_CHANGES` | `true` | En-tête `X-Use-2026-API-Changes` (comportement 2026 de l'API). |

Le token est un `SecretStr` : jamais dans `repr`/`str`, et les en-têtes sensibles
(`Authorization`, `X-Api-Key`, `Cookie`…) sont masqués dans toutes les exceptions.

## Architecture

```
grandlivre/
├── client.py          # httpx async, headers, rate limit, retry, exceptions typées
├── config.py          # Settings (pydantic-settings)
├── exceptions.py      # hiérarchie typée + masquage des en-têtes sensibles
├── filtre.py          # clauses du paramètre `filter` JSON des listes
├── fiscal_years.py    # resolve_fiscal_year(exercice) → bornes de l'exercice
├── pagination.py      # itération par curseur + cap dur
├── parsing.py         # parse_decimal strict (float rejeté)
├── rate_limit.py      # aiolimiter, clé cabinet_id, un limiteur par boucle d'événements
├── retry.py           # tenacity : 429 + 5xx, backoff exponentiel + jitter
├── wal.py             # WAL, stores d'idempotence, réconciliation, recover
├── store_testing.py   # contrat de tests partagé pour les stores
├── models/            # modèles Pydantic v2 (18 modules), extra="allow"
└── resources/         # façades par ressource (17 modules)
```

## Choix de conception

- **Spec OpenAPI comme référence, modèles écrits à la main.** Les modèles sont
  écrits d'après la spec OpenAPI Pennylane V2 et les réponses réelles observées. La
  spec brute est versionnée (`openapi_spec_raw.json`, document publié par
  Pennylane, voir `NOTICE`) : `tests/sentinelle/` détecte une dérive structurelle,
  `tests/test_openapi_errata.py` caractérise les divergences connues de la spec.
- **Secrets locaux.** Token `SecretStr`, en-têtes masqués dans les exceptions,
  `.env` ignoré par Git, aucune dépendance à un coffre distant.
- **Retries prévisibles.** `tenacity` sur 429 et 5xx uniquement ; les autres 4xx
  sont propagés immédiatement. `aiolimiter` limite le débit sortant en amont.
- **Async-first.** Aucune API synchrone : `asyncio.run`, FastAPI, FastMCP…
- **Nommage.** Les concepts comptables sont en français (`exercice`, `cabinet_id`),
  les paramètres techniques en anglais (`date_from`, `cap`, `cursor`).
- **Critère de simplicité.** Entre deux implémentations qui satisfont la spec, la
  plus simple gagne (moins de lignes, d'abstractions, de dépendances) ; voir
  [`CONTRIBUTING.md`](CONTRIBUTING.md#simplicity-criterion).

## Tests

```bash
pytest                      # unitaires (zéro réseau), seuil de couverture appliqué
pytest -m smoke             # intégration contre un dossier réel (PENNYLANE_API_TOKEN requis)
ruff check && ruff format --check && mypy src/grandlivre
```

Pour intégrer `grandlivre` dans un serveur MCP (FastMCP), voir
[`docs/cookbook/mcp-integration.md`](docs/cookbook/mcp-integration.md).

## Feuille de route

Les évolutions sont annoncées dans le CHANGELOG. La promesse de la 1.0.0 : stabilité
de l'API publique (SemVer), corrections et ajouts additifs en versions mineures.

## Contribuer, sécurité, licence

Voir [CONTRIBUTING.md](./CONTRIBUTING.md) (workflow, standards, release) et
[SECURITY.md](./SECURITY.md) (signalement privé des vulnérabilités — pas d'issue
publique). Le projet suit le [Contributor Covenant 2.1](./CODE_OF_CONDUCT.md).

[Apache License 2.0](./LICENSE).
