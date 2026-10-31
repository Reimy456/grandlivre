"""Hiérarchie d'exceptions grandlivre.

Inspiration : mcp_servers/pennylane_read/exceptions.py (origine interne).

Discipline de redaction : toute exception encapsulant une réponse HTTP scrub
systématiquement les headers sensibles (`Authorization`, `X-Api-Key`, `Cookie`,
…) à la construction. Le token Bearer ne doit JAMAIS apparaître dans le
message, le repr, le str ou les headers d'une exception.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

# Headers à scrubber systématiquement avant toute construction d'exception.
# La comparaison s'effectue en minuscules (insensible à la casse).
_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "x-api-key",
        "cookie",
        "set-cookie",
        "proxy-authorization",
    }
)

_REDACTED = "[REDACTED]"

# Hints actionables — pattern inspiré MemPalace `_no_palace()` (cf. audit
# `.claude/audits/2026-05-12_audit_mempalace_deep.md` §3.4). Chaque hint
# donne la commande / le tool / la ressource exacte à invoquer pour réparer.
_HINT_AUTH = "Set/refresh PENNYLANE_API_TOKEN in .env (renouvelable à volonté dans l'UI Pennylane)."
_HINT_RATE_LIMIT = (
    "Quota Pennylane dépassé. Retry géré automatiquement par tenacity "
    "(backoff exponentiel + jitter) ; sinon attendre la fenêtre de reset."
)
_HINT_SERVER = (
    "Erreur serveur Pennylane. Retry géré automatiquement par tenacity ; "
    "si persistant, contacter le support Pennylane."
)
_HINT_CLIENT = (
    "Requête invalide. Vérifier le payload et les paramètres ; corriger côté client avant retry."
)
_HINT_UNUSUAL = (
    "Réponse HTTP hors plage standard (1xx/3xx). Inspecter `body` et `url` ; "
    "vérifier les redirections ou états transitoires API."
)
_HINT_FISCAL_YEAR = (
    "Lister les exercices disponibles via la resource MCP `fiscal_years` "
    "ou l'endpoint API V2 `/fiscal_years`."
)
_HINT_ACCOUNT_NUMBER = (
    "Vérifier le numéro de compte dans le Plan Comptable Pennylane "
    "(resource MCP `ledger_accounts` ou endpoint `/ledger_accounts`)."
)
_HINT_FEC_EXPORT = (
    "Inspecter le job d'export FEC côté Pennylane (status='error' "
    "ou file_url absent après polling). Retry possible ; "
    "si persistant, contacter le support Pennylane."
)
_HINT_UNBALANCED = (
    "Écriture non équilibrée : la somme des débits doit égaler la somme des "
    "crédits (partie double). Vérifier les montants débit/crédit des lignes."
)
_HINT_VALIDATION = (
    "Payload refusé par Pennylane (422) : compte/journal invalide, montants/"
    "dates mal formés, exercice clos ou journal verrouillé. Inspecter `body`."
)


def redact_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Retourne une copie des headers avec valeurs sensibles remplacées par [REDACTED].

    Tolère `None` et retourne un dict vide dans ce cas.
    """
    if headers is None:
        return {}
    return {k: (_REDACTED if k.lower() in _SENSITIVE_HEADERS else v) for k, v in headers.items()}


class PennylaneError(Exception):
    """Racine de la hiérarchie d'erreurs grandlivre.

    Encapsule une réponse HTTP optionnelle (statut, URL nettoyée, corps public,
    headers redactés). Le repr et le str ne peuvent jamais inclure le header
    Authorization brut.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        body: str | None = None,
        headers: Mapping[str, str] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.url = url
        self.body = body
        # Toujours redacter à la construction. Pas de chemin laissant passer un header brut.
        self.headers = redact_headers(headers)
        # Hint actionable — la commande / le tool / la ressource à invoquer pour réparer.
        self.hint = hint

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"message={self.message!r}, "
            f"status_code={self.status_code!r}, "
            f"url={self.url!r}, "
            f"hint={self.hint!r}, "
            f"headers={self.headers!r})"
        )

    def __str__(self) -> str:
        base = (
            self.message
            if self.status_code is None
            else f"[{self.status_code}] {self.message} (url={self.url})"
        )
        if self.hint:
            return f"{base} | hint: {self.hint}"
        return base


class PennylaneAuthError(PennylaneError):
    """401 / 403 — token expiré, absent, ou scope insuffisant."""


class PennylaneRateLimitError(PennylaneError):
    """429 — quota dépassé. Géré par tenacity (backoff exponentiel + jitter)."""


class PennylaneServerError(PennylaneError):
    """5xx — erreur côté Pennylane. Retry également."""


class PennylaneClientError(PennylaneError):
    """4xx (hors 401/403/429) — requête invalide. Non récupérable sans correction du code."""


class PennylaneValidationError(PennylaneClientError):
    """422 — payload refusé par Pennylane (validation métier : compte/journal
    invalide, montants/dates mal formés, exercice clos, journal verrouillé).

    Sous-classe de `PennylaneClientError` (Liskov : reste attrapable comme une
    erreur client 4xx) avec un hint dédié + le `body` Pennylane (RFC 9457). Sur
    un POST write → transition WAL FAILED définitive (pas de retry, pas de
    read-back), cf. ADR.
    """


class FiscalYearNotFoundError(PennylaneError):
    """Exercice demandé absent du référentiel Pennylane /fiscal_years.

    Erreur métier (pas HTTP) : `status_code=None`. L'attribut `exercice` porte
    la valeur de l'exercice non résolu.
    """

    def __init__(self, exercice: str) -> None:
        super().__init__(
            f"Exercice '{exercice}' introuvable dans /fiscal_years.",
            status_code=None,
            hint=_HINT_FISCAL_YEAR,
        )
        self.exercice = exercice


class FECExportFailedError(PennylaneError):
    """Échec d'un export FEC (job API renvoie `status='error'` ou
    `file_url` absent après polling).

    Erreur métier (pas HTTP nécessairement) : `status_code=None` quand
    l'API a renvoyé un job en `status='error'` (réponse HTTP 200 mais
    job en échec côté Pennylane). Voir
    `resources/exports.py::FECExportResource.wait/download`.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        body: str | None = None,
        headers: Mapping[str, str] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            url=url,
            body=body,
            headers=headers,
            hint=hint if hint is not None else _HINT_FEC_EXPORT,
        )


class AccountNumberNotFoundError(PennylaneError):
    """Numéro de compte demandé absent du référentiel Pennylane /ledger_accounts.

    Erreur métier (pas HTTP) : `status_code=None`. L'attribut `account_number`
    porte la valeur du numéro non résolu.
    """

    def __init__(self, account_number: str) -> None:
        super().__init__(
            f"Numéro de compte '{account_number}' introuvable dans /ledger_accounts.",
            status_code=None,
            hint=_HINT_ACCOUNT_NUMBER,
        )
        self.account_number = account_number


class UnbalancedEntryError(PennylaneError):
    """Écriture comptable non équilibrée — somme des débits != somme des crédits.

    Erreur métier (pas HTTP) : `status_code=None`, levée AVANT tout POST
    (validation pré-écriture, ADR). Les attributs `debit_sum`,
    `credit_sum` et `balance` (= debit_sum - credit_sum) portent les montants
    (Decimal, déjà quantizés à la précision de comparaison par l'appelant).
    """

    def __init__(self, debit_sum: Decimal, credit_sum: Decimal) -> None:
        self.debit_sum = debit_sum
        self.credit_sum = credit_sum
        self.balance = debit_sum - credit_sum
        super().__init__(
            f"Écriture non équilibrée : débits={debit_sum} != crédits={credit_sum} "
            f"(écart={self.balance}).",
            status_code=None,
            hint=_HINT_UNBALANCED,
        )


def from_status(
    status_code: int,
    *,
    url: str | None,
    body: str | None,
    headers: Mapping[str, str] | None,
) -> PennylaneError:
    """Construit l'exception typée correspondant à un code HTTP.

    Retour : instance d'une sous-classe de `PennylaneError`, headers redactés.
    """
    if status_code in (401, 403):
        return PennylaneAuthError(
            f"Authentification Pennylane refusée ({status_code})",
            status_code=status_code,
            url=url,
            body=body,
            headers=headers,
            hint=_HINT_AUTH,
        )
    if status_code == 429:
        return PennylaneRateLimitError(
            "Quota Pennylane dépassé",
            status_code=status_code,
            url=url,
            body=body,
            headers=headers,
            hint=_HINT_RATE_LIMIT,
        )
    if 500 <= status_code < 600:
        return PennylaneServerError(
            f"Erreur serveur Pennylane ({status_code})",
            status_code=status_code,
            url=url,
            body=body,
            headers=headers,
            hint=_HINT_SERVER,
        )
    if status_code == 422:
        return PennylaneValidationError(
            "Payload Pennylane refusé (422 — validation)",
            status_code=status_code,
            url=url,
            body=body,
            headers=headers,
            hint=_HINT_VALIDATION,
        )
    if 400 <= status_code < 500:
        return PennylaneClientError(
            f"Requête Pennylane invalide ({status_code})",
            status_code=status_code,
            url=url,
            body=body,
            headers=headers,
            hint=_HINT_CLIENT,
        )
    return PennylaneError(
        f"Réponse Pennylane inattendue ({status_code})",
        status_code=status_code,
        url=url,
        body=body,
        headers=headers,
        hint=_HINT_UNUSUAL,
    )
