# Politique de sécurité

Nous prenons la sécurité de `grandlivre` au sérieux. Cette
bibliothèque manipule des tokens d'API donnant accès à des données
comptables sensibles (ledger, factures, transactions bancaires). Une
vulnérabilité dans le client peut compromettre la confidentialité ou
l'intégrité des données du cabinet utilisateur final.

## Versions supportées

Seule la dernière version `MINOR` de la branche `MAJOR` courante reçoit des
correctifs de sécurité. Tableau mis à jour à chaque release.

| Version | Statut |
|---|---|
| 1.0.0rc / 1.0.x | ✅ Supportée (branche courante) |
| < 1.0.0rc1 | ❌ Aucune release publique antérieure |

Au passage v1.0.0 stable, la politique passera à : version `N` supportée +
version `N-1` supportée pendant 6 mois après la release `N`.

## Signaler une vulnérabilité

**Ne pas ouvrir d'issue GitHub publique pour les problèmes de sécurité.**

Utiliser le canal privé **GitHub Security Advisory** :

1. Aller sur https://github.com/Reimy456/grandlivre/security/advisories.
2. Cliquer sur « Report a vulnerability ».
3. Remplir le formulaire avec :
   - Description du problème.
   - Étapes pour reproduire (sans exposer de token réel).
   - Impact estimé (confidentialité, intégrité, disponibilité).
   - Version(s) affectée(s).
   - Suggestion de correctif si disponible.

GitHub Security Advisory permet une communication chiffrée privée entre le
signaleur et le mainteneur, et une publication coordonnée de l'advisory
après correctif.

## Périmètre

### Dans le périmètre

Toute vulnérabilité affectant la bibliothèque elle-même :

- **Fuite de secrets** : token, header `Authorization`, données sensibles
  exposés dans les logs, exceptions, messages d'erreur, ou via mécanisme
  non documenté.
- **Injection** : injection de paramètres URL, headers, body permettant à
  un caller malveillant d'exécuter des appels non prévus.
- **Validation insuffisante** : acceptation de payloads malformés
  conduisant à corruption d'état ou crash exploitable.
- **Dépendances vulnérables** : CVE dans `httpx`, `pydantic`,
  `pydantic-settings`, `aiolimiter`, `tenacity`, `anyio` affectant
  directement notre usage.
- **Comportement réseau** : envoi de requêtes vers des hosts non
  documentés, désactivation TLS, bypass de validation certificat.

### Hors périmètre

- **Vulnérabilités côté API Pennylane** : à signaler directement à Pennylane
  via leur canal de sécurité officiel
  (https://pennylane.readme.io/docs/security ou support produit).
- **Configuration utilisateur** : token committé en clair dans un repo
  utilisateur, `.env` exposé publiquement, mauvaise gestion des secrets en
  production utilisateur — relève de la responsabilité de l'intégrateur.
## Réponse attendue

Engagement du mainteneur :

| Délai | Action |
|---|---|
| 48 h | Accusé de réception du rapport. |
| 14 jours | Évaluation initiale + classification sévérité (CVSS). |
| 30 jours | Plan de correction confirmé au signaleur. |
| 30 jours | Patch livré pour vulnérabilités critiques (CVSS ≥ 9.0). |
| 90 jours | Patch livré pour vulnérabilités haute/moyenne (CVSS 4.0-8.9). |

Pour les vulnérabilités basses (CVSS < 4.0), correction dans la prochaine
release `MINOR` standard.

Divulgation coordonnée : publication de l'advisory GitHub + entrée
CHANGELOG après livraison du patch. Crédit au signaleur (sauf demande
explicite d'anonymat).

## Bonnes pratiques pour les utilisateurs

Recommandations à appliquer côté intégration :

- **Tokens** : stocker `PENNYLANE_API_TOKEN` dans un secret manager
  (HashiCorp Vault, AWS Secrets Manager, Doppler, etc.) ou minimum dans
  `.env` gitignoré. Jamais en clair dans le code ou un repo public.
- **Rotation** : régénérer le token périodiquement via UI Pennylane, surtout
  après tout incident de sécurité côté votre infrastructure.
- **Scopes** : utiliser le scope minimum nécessaire (Company Token
  `:readonly` si lecture seule suffit).
- **Logs** : la bibliothèque rédacte automatiquement le header
  `Authorization` dans ses exceptions et logs internes. Vérifier que votre
  framework de logging ne capture pas accidentellement les payloads
  complets.
- **Réseau** : si déploiement en production, restreindre les egress aux
  hosts Pennylane V2 (`app.pennylane.com`) au niveau firewall.
- **TLS** : la bibliothèque utilise les CA du système via `httpx` (pas de
  désactivation TLS). Ne pas surcharger avec `verify=False` en production.

## Audit interne

`grandlivre` suit ces pratiques pour réduire la surface
d'attaque :

- **`detect-secrets`** et `ruff` en hooks pre-commit (`.pre-commit-config.yaml`).
- **CI** : `pip-audit` sur les dépendances runtime installées (job
  `pip-audit` de `lint.yml`, à chaque push et pull request).
- **Pas de `pickle`, `eval`, `exec`** sur des données externes.
- **Pas de fichier temporaire avec données sensibles** sans nettoyage
  explicite.
- **Dépendances déclarées avec bornes minimales** (`>=`) dans `pyproject.toml`,
  auditées par `pip-audit` en CI ; pas de version flottante (`*`).
- **Modèles Pydantic v2 stricts** : validation systématique des entrées
  réseau, rejet `float` sur montants monétaires.

## Historique des advisories

Aucun pour le moment.

La liste publique sera maintenue à
https://github.com/Reimy456/grandlivre/security/advisories.
