"""Client httpx async pour l'API Pennylane V2.

Inspiration : mcp_servers/pennylane_read/api_client.py (origine interne).

Responsabilités :
  - Injection des headers (`Authorization: Bearer <token>`,
    `X-Use-2026-API-Changes: true`, `Accept: application/json`).
  - Rate limiting in-process par `cabinet_id` (aiolimiter, cf. rate_limit.py).
  - Retry tenacity sur 429 et 5xx (cf. retry.py).
  - Conversion des codes HTTP en exceptions typées avec redaction des headers
    sensibles (cf. exceptions.py).

Usage :
    async with PennylaneClient(settings=settings) as client:
        data = await client.request("GET", "/trial_balance", params=...)

Le client peut aussi prendre un `http_client` externe (injection de dépendance)
pour les tests : dans ce cas il ne gère pas son cycle de vie.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import TracebackType
from typing import Any

import httpx

from .config import Settings, get_settings
from .exceptions import (
    PennylaneError,
    from_status,
)
from .rate_limit import get_rate_limiter
from .retry import build_retryer, build_write_retryer

logger = logging.getLogger(__name__)


class PennylaneClient:
    """Client async pour l'API Pennylane V2 avec rate limit + retry + redaction.

    Args:
        settings: Configuration (token, base_url, rate_limit, cabinet_id, header
            2026, timeout). Si None, `get_settings()` est appelé.
        http_client: Instance httpx.AsyncClient à injecter (tests). Si fournie,
            son cycle de vie n'est pas géré par PennylaneClient.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=self._settings.pennylane_base_url,
            timeout=httpx.Timeout(self._settings.pennylane_request_timeout_seconds),
        )
        self._limiter = get_rate_limiter(
            cabinet_id=self._settings.pennylane_cabinet_id,
            rate_per_sec=self._settings.pennylane_rate_limit,
        )

    @property
    def settings(self) -> Settings:
        return self._settings

    async def __aenter__(self) -> PennylaneClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    def _build_headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        """Construit les headers obligatoires. NE JAMAIS logger ce dict tel quel."""
        token = self._settings.pennylane_api_token.get_secret_value()
        headers: dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if self._settings.pennylane_use_2026_api_changes:
            headers["X-Use-2026-API-Changes"] = "true"
        if extra:
            headers.update(extra)
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Effectue une requête HTTP avec rate limiting et retry.

        Levée d'exceptions typées (`PennylaneAuthError`, `PennylaneRateLimitError`,
        `PennylaneClientError`, `PennylaneServerError`) construites via
        `from_status` — les headers sensibles y sont déjà redactés.

        Returns:
            Le corps JSON parsé (dict).
        """
        retryer = build_retryer()
        async for attempt in retryer:
            with attempt:
                async with self._limiter:
                    response = await self._http.request(
                        method,
                        path,
                        params=dict(params) if params else None,
                        json=json,
                        headers=self._build_headers(extra_headers),
                    )
                if response.status_code >= 400:
                    # Body tronqué pour éviter données volumineuses dans logs.
                    body_excerpt = response.text[:500] if response.text else None
                    # URL sans query (potentiellement sensible si filtres custom).
                    clean_url = str(response.request.url).split("?", 1)[0]
                    raise from_status(
                        response.status_code,
                        url=clean_url,
                        body=body_excerpt,
                        headers=dict(response.headers),
                    )
                parsed: dict[str, Any] = response.json()
                return parsed
        # Inatteignable : retryer.reraise=True relance la dernière exception.
        # Garde-fou typage strict.
        raise PennylaneError("Échec retry sans exception capturée — état incohérent.")

    async def request_write(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        files: Mapping[str, tuple[str | None, bytes | str, str]] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Requête d'ÉCRITURE write-safe (POST/PUT) — PAS de re-POST aveugle (ADR).
        ``files`` (dépôt multipart, ``POST /file_attachments`` : ``{champ: (nom, octets, type
        MIME)}``) s'emploie SANS ``json`` ; httpx pose alors le ``Content-Type`` multipart
        lui-même (les en-têtes du client ne fixent pas de Content-Type). Un champ de formulaire
        sans fichier (``invoice_options`` de l'import « facture électronique ») s'écrit
        ``(None, texte, type MIME)``.

        À la différence de `request()`, n'utilise PAS `build_retryer()` (qui retente 5xx
        et toute `TransportError` → risque de doublon sur un write). Utilise
        `build_write_retryer()`, qui ne retente que les échecs DÉFINITIVEMENT non-écrits
        (429 + `ConnectError` — connexion JAMAIS établie, octets jamais envoyés ; cf.
        `retry.py` build_write_retryer). Un `ConnectTimeout` (établissement de connexion
        AMBIGU) n'est PAS retenté (revue v2 MF5). Tout 5xx / timeout / erreur transport
        APRÈS envoi est PROPAGÉ : l'appelant (`LedgerEntriesResource.create()`) le
        transitionne en `IN_DOUBT` + read-back de réconciliation (§2.4), jamais de
        re-POST aveugle.

        Exceptions typées (`from_status`) : `422` → `PennylaneValidationError` (FAILED
        déterministe, §2.13) ; autres `4xx` → `PennylaneClientError` (FAILED) ; `5xx` →
        `PennylaneServerError` (IN_DOUBT, NON retenté).
        """
        retryer = build_write_retryer()
        async for attempt in retryer:
            with attempt:
                async with self._limiter:
                    response = await self._http.request(
                        method,
                        path,
                        json=json,
                        files=dict(files) if files else None,
                        headers=self._build_headers(extra_headers),
                    )
                if response.status_code >= 400:
                    body_excerpt = response.text[:500] if response.text else None
                    clean_url = str(response.request.url).split("?", 1)[0]
                    raise from_status(
                        response.status_code,
                        url=clean_url,
                        body=body_excerpt,
                        headers=dict(response.headers),
                    )
                # 204 No Content / corps vide : certains writes Pennylane (matched_transactions,
                # payment_status, mark_as_paid — B-K9) accusent réception SANS corps. `.json()`
                # lèverait sur un body vide → on renvoie {} (POST /ledger_entries, lui, répond
                # 201 + corps, donc non concerné). `request()` (read) garde son `.json()`.
                if response.status_code == 204 or not response.content:
                    return {}
                parsed: dict[str, Any] = response.json()
                return parsed
        raise PennylaneError("Échec retry write sans exception capturée — état incohérent.")
