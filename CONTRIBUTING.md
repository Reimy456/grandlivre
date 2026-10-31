# Contributing to grandlivre

Merci de votre intérêt pour contribuer à `grandlivre`. Ce document
décrit le workflow de contribution, les standards de code et le processus de
release.

## Code of Conduct

Ce projet adhère au [Contributor Covenant](./CODE_OF_CONDUCT.md). En
participant, vous acceptez de respecter ce code. Signalez tout comportement
inacceptable au mainteneur du projet via la procédure décrite dans
[SECURITY.md](./SECURITY.md) (canal privé GitHub Security Advisory).

## Signaler un bug ou demander une fonctionnalité

- **Bugs** : ouvrir une issue sur [GitHub Issues](https://github.com/Reimy456/grandlivre/issues).
  Fournir :
  - Version de la bibliothèque (`pip show grandlivre`).
  - Version Python (`python --version`).
  - Endpoint API concerné.
  - Trace d'erreur complète (en redactant tout `Authorization: Bearer …` ou
    autre token).
  - Étapes minimales pour reproduire.
- **Vulnérabilités de sécurité** : **ne pas ouvrir d'issue publique**. Voir
  [SECURITY.md](./SECURITY.md).
- **Fonctionnalités** : ouvrir une issue avec le label `enhancement` et
  décrire le cas d'usage métier (comptabilité française, expertise comptable,
  trésorerie, etc.).

## Workflow de contribution

### 1. Fork & clone

```bash
git clone https://github.com/<votre-fork>/grandlivre.git
cd grandlivre
git remote add upstream https://github.com/Reimy456/grandlivre.git
```

### 2. Setup environnement

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
pip install -e ".[test,dev]"
```

### 3. Branche dédiée

```bash
git checkout -b feat/<sujet-court>     # nouvelle fonctionnalité
git checkout -b fix/<sujet-court>      # correction de bug
git checkout -b docs/<sujet-court>     # documentation
git checkout -b test/<sujet-court>     # tests uniquement
git checkout -b refactor/<sujet-court> # refactoring sans changement de comportement
```

### 4. Convention de commits (Conventional Commits)

Format : `<type>(<scope>): <description courte>`.

Types acceptés :
- `feat` — nouvelle fonctionnalité (resource, méthode publique, option).
- `fix` — correction de bug.
- `docs` — documentation uniquement.
- `test` — ajout ou correction de tests.
- `refactor` — refactoring sans changement de comportement observable.
- `chore` — outillage, build, CI.
- `perf` — amélioration de performance.

Scope suggérés : `resource:<nom>`, `client`, `pagination`, `parsing`, `config`,
`exceptions`, `rate_limit`, `retry`, `models:<nom>`, `tests`, `ci`, `deps`.

Exemples :
```
feat(resource:journals): add /journals/{id} get method
fix(pagination): handle null next_cursor on truncated page
docs(readme): clarify 2026 API migration timeline
test(resource:fec): add async polling failure scenario
```

Breaking changes : préfixer avec `!` après le scope (`feat(client)!: ...`) et
documenter en footer (`BREAKING CHANGE: ...`). Les breaking changes
nécessitent un bump major (v2.0.0) après publication v1.0.0.

### 5. Standards de code

#### Tests unitaires (obligatoires)

Tout nouveau code doit être couvert par des tests unitaires `respx`
(zero-network). Pattern :

```python
import respx
import httpx
import pytest


@pytest.mark.asyncio
async def test_resource_handles_empty_page(client_factory):
    async with client_factory() as client:
        respx.get("https://app.pennylane.com/api/external/v2/journals").mock(
            return_value=httpx.Response(200, json={"items": [], "next_cursor": None})
        )
        result = await JournalsResource(client).list()
        assert result == []
```

Minimum 5 cas par nouvelle resource :
- Page unique succès.
- Multi-pages (cursor pagination).
- Page vide.
- Erreur 4xx (404, 401, 422).
- Erreur 5xx + retry tenacity.

Coverage minimum maintenu : **96 %** sur `src/grandlivre/`.
Vérifier avec `pytest --cov`.

#### Hooks pre-commit (recommandés)

```bash
pip install pre-commit && pre-commit install
```

`ruff` (check + format) et `detect-secrets` s'exécutent alors avant chaque commit
(configuration : `.pre-commit-config.yaml`). Les captures de réponses réelles
(`tests/smoke/fixtures/`) sont ignorées par Git : ne jamais les forcer.

#### Tests smoke (optionnels)

Pour les endpoints critiques (FEC, exports légaux), un test smoke sandbox
optionnel peut être ajouté dans `tests/smoke/`. Marqueur `@pytest.mark.smoke`
obligatoire — exclus du run par défaut, exécutés uniquement avec `pytest -m smoke`
et un `PENNYLANE_API_TOKEN` configuré.

#### Linting & formatting

```bash
ruff check                  # lint
ruff format                 # format
ruff format --check          # vérification CI
```

Configuration dans `pyproject.toml` `[tool.ruff]`. `line-length = 100`,
target Python 3.12.

#### Type checking

```bash
mypy src/grandlivre
```

Configuration : `[tool.mypy]` strict, Python 3.12. Pas de `# type: ignore`
sans commentaire justifiant l'ignore.

#### Décimales et montants

**Pas de `float`** sur les montants monétaires. Utiliser `decimal.Decimal` via
`grandlivre.parsing.parse_decimal`. Le wrapper rejette
implicitement les `float` en entrée. Les modèles Pydantic v2 sont stricts.

#### Modèles Pydantic

`extra="allow"` par défaut sur tous les modèles, pour résilience aux
évolutions de spec API Pennylane. Champs canoniques typés, champs additionnels
acceptés sans erreur.

#### Pas de stub `NotImplementedError`

Doctrine no-debt : un endpoint est soit livré (resource + model + tests),
soit absent du package. Pas de placeholder.

#### Simplicity criterion

Règle de décision binaire pour toute revue de PR : entre deux
implémentations qui satisfont la spec fonctionnelle (mêmes endpoints couverts,
mêmes tests verts), choisir systématiquement celle dont le code est le plus
simple — moins de lignes, moins d'abstractions, moins de dépendances, moins de
cas limites à raisonner. Retirer du code à résultat égal est un gain ; ajouter de
la complexité pour un gain marginal est une perte. La simplicité prime sur
l'élégance, sur la généricité hypothétique, sur la performance non mesurée.

**Critère opérationnel en revue PR** : chaque diff doit pouvoir
répondre à la question « est-ce l'implémentation la plus simple qui
satisfait la spec ? ». Si non, justifier en message de commit ou en
commentaire pourquoi la complexité ajoutée est requise (cas concret
documenté, mesure de performance, contrainte externe — pas
« extensibilité future » non sourcée).

**Red flag à intercepter** : « on pourrait ajouter une couche
d'abstraction au cas où… » sans cas d'usage concret. Refuser.
Cohérent avec la doctrine **no-debt** (pas de stub, pas
d'abstraction prématurée).

### 6. Mise à jour de la documentation

- **CHANGELOG.md** : entrée obligatoire dans `[Unreleased]` pour tout commit
  `feat`, `fix`, `refactor` (perf inclus). `docs`/`test`/`chore` optionnel.
- **README.md** : mettre à jour la section « Statut » si un nouvel endpoint est
  ajouté à la liste exhaustive.
- **Docstrings** : toutes les méthodes publiques (sans underscore) doivent
  avoir une docstring décrivant paramètres, retour, exceptions levées.
- **Cookbook `docs/cookbook/mcp-integration.md`** : si vous modifiez un pattern
  d'intégration côté serveur MCP (settings, tools, pagination, redaction logs),
  mettre à jour la recette correspondante. Toute nouvelle recette
  (intégration différente, transport alternatif, etc.) s'ajoute en section
  dédiée.

### 7. Pull request

```bash
git push origin <votre-branche>
```

Puis ouvrir une PR sur GitHub vers `main`. Template attendu :

```markdown
## Résumé

<une phrase>

## Périmètre

- <fichiers touchés, scope endpoint, etc.>

## Tests

- <nb tests ajoutés, scénarios couverts>

## Breaking changes

<si applicable, sinon "Aucun">

## Checklist

- [ ] Tests unitaires verts (`pytest`)
- [ ] Coverage ≥ 96 % (`pytest --cov`)
- [ ] Linting OK (`ruff check`)
- [ ] Type checking OK (`mypy`)
- [ ] CHANGELOG.md mis à jour
- [ ] README.md mis à jour (si nouvel endpoint)
```

CI obligatoire pour merge :
- `test.yml` — pytest sur Python 3.12 et 3.13.
- `lint.yml` — `ruff check` + `ruff format --check` + `mypy --strict` + `pip-audit`.

## Processus de release

Releases SemVer (`MAJOR.MINOR.PATCH`).

### Workflow

1. Mettre à jour `CHANGELOG.md` : déplacer entrées `[Unreleased]`
   sous `[X.Y.Z] - YYYY-MM-DD`.
2. Mettre à jour `pyproject.toml` `version = "X.Y.Z"`.
3. Commit : `chore(release): vX.Y.Z`.
4. Tag : `git tag vX.Y.Z && git push --tags`.
5. Le workflow `release.yml` publie automatiquement sur PyPI via
   [Trusted Publishers](https://docs.pypi.org/trusted-publishers/) (OIDC, pas
   de token PyPI à gérer).
6. Création automatique d'une GitHub Release avec extrait CHANGELOG.

### Pre-releases

- `vX.Y.Z-rc.N` (release candidates) → TestPyPI uniquement.
- `vX.Y.Z-alpha.N` / `vX.Y.Z-beta.N` → TestPyPI uniquement.

### Critères de bump

- **PATCH** (`vX.Y.Z+1`) : bug fixes, corrections de typing, refactor interne
  sans impact public.
- **MINOR** (`vX.Y+1.0`) : nouveau endpoint, nouvelle option, nouveau modèle.
  Rétro-compatible.
- **MAJOR** (`vX+1.0.0`) : breaking change (renommage public, suppression,
  changement de signature). Documentation de migration obligatoire.

## Architecture du projet

Voir `README.md` section « Architecture ».

Points d'extension fréquents :
- **Ajouter un endpoint** : créer `src/grandlivre/resources/<nom>.py`
  + `src/grandlivre/models/<nom>.py`. Tests `tests/test_<nom>.py`.
- **Ajouter un champ à un modèle** : modèle Pydantic v2 `extra="allow"`
  accepte tout, mais expliciter les champs métier dans le modèle.
- **Ajouter une exception** : héritage `grandlivre.exceptions.PennylaneError`
  + sous-classe spécifique. Redaction `Authorization` héritée
  automatiquement.

## Doctrine de revue

Les PR sont revues sur 3 axes :

1. **Correction fonctionnelle** : tests verts, coverage maintenu, smoke
   sandbox éventuel passant.
2. **Cohérence d'API** : signature alignée avec les resources existantes
   (`list`/`iterate`/`paginate`/`get`), Pydantic v2 strict, gestion d'erreurs
   typée.
3. **Documentation** : docstrings, README, CHANGELOG, exemples si pertinent.

Réponse mainteneur : sous 7 jours ouvrés. PRs sans activité contributeur après
30 jours peuvent être fermées (réouverture sur demande).

## Licence

En contribuant à ce projet, vous acceptez que vos contributions soient
distribuées sous la [licence Apache 2.0](./LICENSE).
