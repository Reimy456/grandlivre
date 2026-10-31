"""Resource façade `/exports/fecs` (v0.3 B.6).

**Pattern asynchrone POST + polling GET + download** (obligation légale
française CGI 286 I bis).

Cycle de vie :
  1. `create(period_start, period_end)` → POST `/exports/fecs` → `FECExportJob`
     avec `status='pending'`.
  2. `status(export_id)` → GET `/exports/fecs/{id}` → `FECExportJob` mis à jour.
  3. `wait(export_id, poll_interval, timeout)` → boucle polling jusqu'à
     `ready` / `error` / timeout. Utilise `asyncio.sleep` (pas
     `time.sleep`).
  4. `download(export_id)` → GET `file_url` (URL externe signée 30 min,
     pas de Bearer Pennylane requis). Retourne `bytes`.

**Bloqueur scope sandbox actuel** : Company Token courant n'a pas le
scope `ledger` requis (HTTP 403 attendu sur POST `/exports/fecs`,
dérivé du cas B.7). Scope ajouté lors du renouvellement Company
Token 2026-05-29 (ROADMAP §4.3). Tests respx mockent les
réponses — pas de smoke sandbox tant que scope absent.

Schéma sourcé `openapi_spec_raw.json` zones
`paths./api/external/v2/exports/fecs` (POST) et
`paths./api/external/v2/exports/fecs/{id}` (GET).
"""

from __future__ import annotations

import asyncio
import time

import httpx

from ..client import PennylaneClient
from ..exceptions import FECExportFailedError
from ..models.exports import (
    AnalyticalGeneralLedgerExportJob,
    FECExportJob,
    GeneralLedgerExportJob,
)

# Valeurs par défaut polling. 2 s alignées sur la latence typique d'un
# job FEC asynchrone (ordre de grandeur : quelques secondes à
# quelques dizaines de secondes pour un mois d'écritures). Timeout
# 300 s = 5 min, marge pour exercices complets.
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_POLL_TIMEOUT_SECONDS = 300.0


class FECExportResource:
    """Façade métier pour `/exports/fecs` (asynchrone POST + polling)."""

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    async def create(
        self,
        period_start: str,
        period_end: str,
    ) -> FECExportJob:
        """POST `/exports/fecs` — crée un job d'export FEC.

        Args:
            period_start: Date début de période (ISO `YYYY-MM-DD`).
            period_end: Date fin de période (ISO `YYYY-MM-DD`).

        Returns:
            `FECExportJob` avec `status='pending'` (initial).
        """
        response = await self._client.request(
            "POST",
            "/exports/fecs",
            json={
                "period_start": period_start,
                "period_end": period_end,
            },
        )
        return FECExportJob.model_validate(response)

    async def status(self, export_id: int) -> FECExportJob:
        """GET `/exports/fecs/{id}` — état courant du job."""
        response = await self._client.request("GET", f"/exports/fecs/{export_id}")
        return FECExportJob.model_validate(response)

    async def wait(
        self,
        export_id: int,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        timeout: float = DEFAULT_POLL_TIMEOUT_SECONDS,
    ) -> FECExportJob:
        """Polling jusqu'à `status='ready'` ou `'error'`.

        Args:
            export_id: ID du job (issu de `create`).
            poll_interval: Délai entre deux GET (secondes).
            timeout: Délai max global avant `TimeoutError` (secondes).

        Returns:
            `FECExportJob` final (status='ready').

        Raises:
            FECExportFailedError: si l'API renvoie `status='error'`.
            TimeoutError: si `timeout` dépassé avant `ready`/`error`.
        """
        # `time.monotonic` plutôt que `asyncio.get_event_loop().time()`
        # pour faciliter le monkeypatch en tests (cf.
        # `tests/resources/test_exports.py`).
        deadline = time.monotonic() + timeout
        while True:
            job = await self.status(export_id)
            if job.status == "ready":
                return job
            if job.status == "error":
                raise FECExportFailedError(
                    f"Export FEC {export_id} a échoué (status='error').",
                    status_code=None,
                )
            # status == "pending" → poursuit le polling
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Export FEC {export_id} non prêt après {timeout}s "
                    f"(dernier status='{job.status}')."
                )
            await asyncio.sleep(poll_interval)

    async def download(self, export_id: int) -> bytes:
        """Télécharge le payload FEC final.

        Récupère d'abord le job (pour obtenir `file_url`), puis fait un
        GET externe sur l'URL signée. L'URL expire après 30 min
        (`file_url` description openapi spec).

        Args:
            export_id: ID du job (doit être `status='ready'`).

        Returns:
            Contenu binaire du fichier FEC.

        Raises:
            FECExportFailedError: si `file_url` est absent (job non prêt).
        """
        job = await self.status(export_id)
        if not job.file_url:
            raise FECExportFailedError(
                f"Export FEC {export_id} n'a pas de file_url (status='{job.status}').",
                status_code=None,
            )
        # URL externe signée — pas de Bearer Pennylane requis, pas de
        # rate limit pertinent (CDN). Client httpx dédié, timeout
        # cohérent avec le client principal.
        # `follow_redirects=True` (lot 0 B0.2, 2026-09-17) : en réel, `file_url`
        # (`app.pennylane.com/rails/active_storage/...`) répond 302 vers le stockage
        # S3 de Pennylane (hôte `*.s3.eu-west-1.amazonaws.com`, prouvé sur le dossier
        # test le 2026-09-16) ; sans suivi, `raise_for_status` levait sur le 302.
        # Même correctif sur les deux exports grand livre (pattern cloné).
        timeout = self._client.settings.pennylane_request_timeout_seconds
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http:
            response = await http.get(job.file_url)
            response.raise_for_status()
            return response.content


# ─── B.13 Grands livres standard et analytique (v0.3) ────────────────────────
#
# **Pattern strictement identique B.6 FEC** (cahier des charges §4.2 B.13).
# Polling cycle, exception class, defaults d'intervalle/timeout, et stratégie
# de download (URL signée externe) sont strictement les mêmes.
#
# Choix de factorisation : **répétition contrôlée** plutôt qu'une classe de
# base abstraite. Rationale :
#   - 2 endpoints supplémentaires seulement (faible duplication absolue,
#     ~30 lignes par resource).
#   - Préserve l'auto-complétion / grep-ability fine par endpoint.
#   - Évite tout risque de régression sur `FECExportResource` (déjà 11/11
#     tests verts au moment de la livraison B.13).
#   - L'écart d'API entre les 3 exports (request body, type retour) reste
#     local à chaque resource sans surcharge méthode/Generics.
#
# Le seul écart fonctionnel inter-resource est le **payload POST** (analytical
# ajoute `mode`) et la classe Pydantic retournée. Le polling / wait / download
# clone strictement la logique FEC.
#
# **Bloqueur scope sandbox** : corollaire B.6 — Company Token courant n'a
# probablement pas le scope `ledger` requis (HTTP 403 attendu sur POST,
# dérivé empiriquement B.6 et B.7). Renouvellement Company Token 2026-05-29
# (ROADMAP §4.3) embarque les scopes nécessaires.


class GeneralLedgerExportResource:
    """Façade métier pour `/exports/general_ledgers` (asynchrone POST + polling).

    Pattern identique `FECExportResource` (cf. docstring module). Request
    body : 2 champs (`period_start`, `period_end`). Response identique.
    """

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    async def create(
        self,
        period_start: str,
        period_end: str,
    ) -> GeneralLedgerExportJob:
        """POST `/exports/general_ledgers` — crée un job d'export grand livre.

        Args:
            period_start: Date début de période (ISO `YYYY-MM-DD`).
            period_end: Date fin de période (ISO `YYYY-MM-DD`).

        Returns:
            `GeneralLedgerExportJob` avec `status='pending'` (initial).
        """
        response = await self._client.request(
            "POST",
            "/exports/general_ledgers",
            json={
                "period_start": period_start,
                "period_end": period_end,
            },
        )
        return GeneralLedgerExportJob.model_validate(response)

    async def status(self, export_id: int) -> GeneralLedgerExportJob:
        """GET `/exports/general_ledgers/{id}` — état courant du job."""
        response = await self._client.request("GET", f"/exports/general_ledgers/{export_id}")
        return GeneralLedgerExportJob.model_validate(response)

    async def wait(
        self,
        export_id: int,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        timeout: float = DEFAULT_POLL_TIMEOUT_SECONDS,
    ) -> GeneralLedgerExportJob:
        """Polling jusqu'à `status='ready'` ou `'error'`.

        Raises:
            FECExportFailedError: si l'API renvoie `status='error'`.
                Exception réutilisée (classe générique « export
                Pennylane échoué », non spécifique FEC).
            TimeoutError: si `timeout` dépassé avant `ready`/`error`.
        """
        deadline = time.monotonic() + timeout
        while True:
            job = await self.status(export_id)
            if job.status == "ready":
                return job
            if job.status == "error":
                raise FECExportFailedError(
                    f"Export grand livre {export_id} a échoué (status='error').",
                    status_code=None,
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Export grand livre {export_id} non prêt après "
                    f"{timeout}s (dernier status='{job.status}')."
                )
            await asyncio.sleep(poll_interval)

    async def download(self, export_id: int) -> bytes:
        """Télécharge le payload grand livre final.

        Raises:
            FECExportFailedError: si `file_url` est absent (job non prêt).
        """
        job = await self.status(export_id)
        if not job.file_url:
            raise FECExportFailedError(
                f"Export grand livre {export_id} n'a pas de file_url (status='{job.status}').",
                status_code=None,
            )
        timeout = self._client.settings.pennylane_request_timeout_seconds
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http:
            response = await http.get(job.file_url)
            response.raise_for_status()
            return response.content


class AnalyticalGeneralLedgerExportResource:
    """Façade métier pour `/exports/analytical_general_ledgers`.

    Pattern identique `FECExportResource` (cf. docstring module). Request
    body : **3 champs** (`period_start`, `period_end`, `mode`). Response
    identique aux autres exports.
    """

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    async def create(
        self,
        period_start: str,
        period_end: str,
        mode: str,
    ) -> AnalyticalGeneralLedgerExportJob:
        """POST `/exports/analytical_general_ledgers` — crée un job.

        Args:
            period_start: Date début de période (ISO `YYYY-MM-DD`).
            period_end: Date fin de période (ISO `YYYY-MM-DD`).
            mode: Axe analytique (str — typage évolutif côté client,
                  l'enum API n'est pas figé spec OpenAPI capturée).

        Returns:
            `AnalyticalGeneralLedgerExportJob` avec `status='pending'`.
        """
        response = await self._client.request(
            "POST",
            "/exports/analytical_general_ledgers",
            json={
                "period_start": period_start,
                "period_end": period_end,
                "mode": mode,
            },
        )
        return AnalyticalGeneralLedgerExportJob.model_validate(response)

    async def status(self, export_id: int) -> AnalyticalGeneralLedgerExportJob:
        """GET `/exports/analytical_general_ledgers/{id}` — état courant."""
        response = await self._client.request(
            "GET", f"/exports/analytical_general_ledgers/{export_id}"
        )
        return AnalyticalGeneralLedgerExportJob.model_validate(response)

    async def wait(
        self,
        export_id: int,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        timeout: float = DEFAULT_POLL_TIMEOUT_SECONDS,
    ) -> AnalyticalGeneralLedgerExportJob:
        """Polling jusqu'à `status='ready'` ou `'error'`.

        Raises:
            FECExportFailedError: si l'API renvoie `status='error'`.
            TimeoutError: si `timeout` dépassé.
        """
        deadline = time.monotonic() + timeout
        while True:
            job = await self.status(export_id)
            if job.status == "ready":
                return job
            if job.status == "error":
                raise FECExportFailedError(
                    f"Export grand livre analytique {export_id} a échoué (status='error').",
                    status_code=None,
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Export grand livre analytique {export_id} non prêt "
                    f"après {timeout}s (dernier status='{job.status}')."
                )
            await asyncio.sleep(poll_interval)

    async def download(self, export_id: int) -> bytes:
        """Télécharge le payload grand livre analytique final."""
        job = await self.status(export_id)
        if not job.file_url:
            raise FECExportFailedError(
                f"Export grand livre analytique {export_id} n'a pas de "
                f"file_url (status='{job.status}').",
                status_code=None,
            )
        timeout = self._client.settings.pennylane_request_timeout_seconds
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http:
            response = await http.get(job.file_url)
            response.raise_for_status()
            return response.content
