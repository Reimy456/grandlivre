"""Politique de retry tenacity centralisée.

Inspiration : mcp_servers/pennylane_read/api_client.py:157-164 (origine interne).

Retry exponentiel avec jitter sur :
  - `PennylaneRateLimitError` (429)
  - `PennylaneServerError` (5xx)
  - `httpx.TransportError` (erreur réseau, timeout)

Les `PennylaneClientError` (4xx hors 429) ne sont PAS retentés — la requête
est invalide, retry ne corrigera rien.
"""

from __future__ import annotations

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .exceptions import PennylaneRateLimitError, PennylaneServerError

# Constantes par défaut. Surchargeables via build_retryer si nécessaire.
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_INITIAL_WAIT_SECONDS = 1.0
DEFAULT_MAX_WAIT_SECONDS = 30.0


def build_retryer(
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_wait: float = DEFAULT_INITIAL_WAIT_SECONDS,
    max_wait: float = DEFAULT_MAX_WAIT_SECONDS,
) -> AsyncRetrying:
    """Construit un retryer tenacity prêt à l'emploi.

    `reraise=True` : la dernière exception est relevée au caller après épuisement
    des tentatives, plutôt qu'une `RetryError` opaque.
    """
    return AsyncRetrying(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=initial_wait, max=max_wait),
        retry=retry_if_exception_type(
            (PennylaneRateLimitError, PennylaneServerError, httpx.TransportError)
        ),
    )


def build_write_retryer(
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_wait: float = DEFAULT_INITIAL_WAIT_SECONDS,
    max_wait: float = DEFAULT_MAX_WAIT_SECONDS,
) -> AsyncRetrying:
    """Retryer WRITE-SAFE (ADR) — anti-doublon.

    À la différence de `build_retryer`, ne retente QUE les échecs qui n'ont
    DÉFINITIVEMENT pas écrit :
      - `PennylaneRateLimitError` (429) : requête rejetée avant traitement ;
      - `httpx.ConnectError` : connexion jamais établie, octets jamais envoyés.

    PAS de re-POST sur `httpx.ConnectTimeout` (revue v2, MF5) : un timeout
    d'établissement de connexion est traité comme « peut-être envoyé » (certaines couches
    proxy/LB peuvent masquer une requête partie) → l'appelant bascule read-back / `IN_DOUBT`,
    jamais de re-POST aveugle. De même, JAMAIS de re-POST sur `PennylaneServerError` (5xx) ni
    sur un timeout/erreur transport POST-envoi : l'écriture a pu atterrir → l'appelant
    (`LedgerEntriesResource.create()`) transitionne `IN_DOUBT` + read-back de
    réconciliation (§2.4), jamais de re-POST aveugle. Plus conservateur que le
    « 5xx » littéral §2.5 : un 5xx-réponse implique que le serveur a reçu la requête
    (a pu committer avant d'errorer) → re-POST = risque de doublon.
    """
    return AsyncRetrying(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=initial_wait, max=max_wait),
        retry=retry_if_exception_type((PennylaneRateLimitError, httpx.ConnectError)),
    )
