"""Resource façade `/ledger_entry_lines`.

Orchestre trois endpoints :
  1. `/fiscal_years` pour résoudre exercice → `{id, start, finish}`.
  2. `/ledger_accounts` pour résoudre `account_number` → `ledger_account_id`
     (uniquement si fourni).
  3. `/ledger_entry_lines` (plat) pour les lignes, paginé via cursor avec cap.

Sémantique de surcharge : si `date_from` / `date_to` sont fournis, ils
surchargent les bornes de l'exercice dans le filtre envoyé.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from pydantic import ValidationError

from ..client import PennylaneClient
from ..exceptions import (
    AccountNumberNotFoundError,
    PennylaneAuthError,
    PennylaneClientError,
    PennylaneError,
    PennylaneRateLimitError,
    PennylaneServerError,
    UnbalancedEntryError,
)
from ..fiscal_years import resolve_fiscal_year
from ..models.ledger_entries import (
    LedgerEntriesPage,
    LedgerEntryLine,
    LedgerSummary,
)
from ..models.ledger_entries_write import (
    CreatedLedgerEntry,
    CreatedLedgerEntryLine,
    NewLedgerEntry,
    NewLedgerEntryLine,
)
from ..pagination import paginate, paginate_with_cap
from ..parsing import parse_decimal
from ..wal import (
    IdempotencyStore,
    NullStore,
    WALDuplicatePayloadError,
    WALInDoubtError,
    WALManager,
    WALState,
    WALStoreError,
    WALStoreRequiredError,
)

# Cap dur par défaut. Aligné sur 5 pages × limit=100 côté API pour éviter le
# dépassement intra-page lors de la troncature.
DEFAULT_HARD_CAP = 500
DEFAULT_PAGE_LIMIT = 100

# Délai de grâce avant le read-back de réconciliation (atténue l'arrivée tardive : un
# POST expiré chez nous peut atterrir chez Pennylane juste après — on lui laisse le
# temps de se poser avant de conclure 0-match). Patché no-op en test (sleep désactivé).
RECONCILE_GRACE_SECONDS = 1.0
# RES-1 — grâce DÉDIÉE entre les 2 sondes 404 du court-circuit : le lag
# read-after-write peut dépasser la grâce de réconciliation → on attend NETTEMENT plus
# longtemps avant d'oser conclure « supprimée ». Patchée no-op en test.
DELETE_PROBE_GRACE_SECONDS = 5.0
# RES-1 — garde d'ÂGE : un double-404 sur une écriture confirmée il y a MOINS de ce délai
# est suspect (lag possible, l'écriture est PROBABLEMENT vivante) → IN_DOUBT gardé
# (résolution humaine), JAMAIS d'invalidation + ré-écriture automatiques. Au-delà, une
# écriture longuement visible qui 404 deux fois = suppression réelle (UI) → heal.
DEAD_ID_AGE_GUARD_SECONDS = 600.0
# Bug B (revue v2) : le read-back identifie NOTRE écriture par un repère d'identifiant
# SERVEUR capturé pré-POST (id-watermark, GET sort=-id) — tout candidat d'id > id_watermark
# est forcément le nôtre. AUCUNE comparaison de timestamps : l'ancienne fenêtre created_at
# (horloge locale vs serveur) introduisait un doublon par skew d'horloge — supprimée.

_Q = Decimal("0.01")


class _DeadConfirmedIdError(Exception):
    """INTERNE (jamais exportée,) : le court-circuit a détecté un mapping CONFIRMED
    dont le ``pennylane_id`` n'existe PLUS côté Pennylane (2× 404 espacés — écriture
    supprimée via l'UI ; l'API V2 n'expose pas de ``DELETE /ledger_entries``, spec + sonde
    validation réelle du 2026-06-10). ``confirmed_at`` (ISO) porte l'instant du confirm mémorisé —
    garde d'âge RES-1 : jeune → IN_DOUBT gardé ; vieille → invalidation + rejeu UNE fois."""

    def __init__(self, fingerprint: str, dead_id: int, confirmed_at: str) -> None:
        super().__init__(f"id confirmé introuvable côté Pennylane : {dead_id}")
        self.fingerprint = fingerprint
        self.dead_id = dead_id
        self.confirmed_at = confirmed_at


class LedgerEntriesResource:
    """Façade métier pour `/ledger_entry_lines`."""

    def __init__(
        self, client: PennylaneClient, *, wal_store: IdempotencyStore | None = None
    ) -> None:
        self._client = client
        # Store WAL injecté par l'appelant (la couche applicative injecte un store réel
        # réel ; JAMAIS NullStore en prod). WALManager construit PARESSEUSEMENT au 1er
        # create() → l'usage lecture seule (paginate/list) ne déclenche AUCUN warning
        # NullStore. Les verrous (ns,fp) persistent ensuite entre appels create().
        self._wal_store = wal_store
        self._wal: WALManager | None = None

    def _get_wal(self) -> WALManager:
        """WALManager paresseux + mémoïsé (verrous persistants entre appels create())."""
        if self._wal is None:
            self._wal = WALManager(self._wal_store or NullStore())
        return self._wal

    async def _resolve_account_number(self, account_number: str) -> int:
        filter_clauses = [{"field": "number", "operator": "eq", "value": account_number}]
        response = await self._client.request(
            "GET",
            "/ledger_accounts",
            params={"filter": json.dumps(filter_clauses)},
        )
        items = response.get("items", [])
        if not items:
            raise AccountNumberNotFoundError(account_number)
        return int(items[0]["id"])

    @staticmethod
    def _transform_line(item: dict[str, Any]) -> LedgerEntryLine:
        """Transforme un item brut `/ledger_entry_lines` vers `LedgerEntryLine`.

        Schéma API Pennylane V2 (comportement 2026, par défaut depuis le
        2026-04-08, capture réelle confirmée le 2026-07-09) : `date`, `label` et
        `journal` sont au niveau de l'item ; `ledger_entry` se réduit à `{id}`.
        Les en-têtes d'écriture (libellé, numéro de facture, numéro de pièce,
        échéance) ne sont pas exposés sur la ligne : voir
        `LedgerEntriesGroupedResource` (`/ledger_entries`).

        Conventions :
          - `debit` / `credit` en string décimale → Decimal quantize 0.01.
          - `ledger_account.number` (string) → `account_number` (code PCG).
          - `lettered_ledger_entry_lines.ids` → `lettered_line_ids` (forme réelle :
            `{"ids": [42, 43], "url": "…"}`, `{"ids": [], …}` pour
            une ligne non lettrée ; clé absente → liste vide).
        """
        entry = item.get("ledger_entry") or {}
        ledger_account = item.get("ledger_account") or {}
        journal_obj = item.get("journal") or {}
        lettered = item.get("lettered_ledger_entry_lines") or {}

        debit = parse_decimal(item.get("debit", "0")).quantize(_Q)
        credit = parse_decimal(item.get("credit", "0")).quantize(_Q)

        return LedgerEntryLine(
            entry_id=entry.get("id"),
            entry_date=item.get("date"),
            journal_id=journal_obj.get("id"),
            line_id=int(item["id"]),
            line_label=item.get("label", "") or "",
            account_number=ledger_account.get("number", "") or "",
            debit=debit,
            credit=credit,
            lettered_line_ids=[int(i) for i in lettered.get("ids") or []],
        )

    def _build_filter_params(
        self,
        *,
        effective_date_from: str,
        effective_date_to: str,
        ledger_account_id: int | None,
        journal_id: int | None,
    ) -> dict[str, Any]:
        filter_clauses: list[dict[str, Any]] = [
            {"field": "date", "operator": "gteq", "value": effective_date_from},
            {"field": "date", "operator": "lteq", "value": effective_date_to},
        ]
        if ledger_account_id is not None:
            filter_clauses.append(
                {"field": "ledger_account_id", "operator": "eq", "value": ledger_account_id}
            )
        if journal_id is not None:
            filter_clauses.append({"field": "journal_id", "operator": "eq", "value": journal_id})
        return {
            "filter": json.dumps(filter_clauses),
            "limit": DEFAULT_PAGE_LIMIT,
        }

    async def paginate(
        self,
        exercice: str,
        *,
        account_number: str | None = None,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        journal_id: int | None = None,
        cursor: str | None = None,
        cap: int = DEFAULT_HARD_CAP,
    ) -> LedgerEntriesPage:
        """Renvoie une page de lignes d'écritures avec cap dur configurable.

        Pagination de continuation : si `truncated=True` est retourné, ré-invoquer
        avec le même `exercice` et les mêmes filtres, en passant `cursor` =
        `next_cursor` reçu. La séquence se termine quand `truncated=False` et
        `next_cursor=None`.
        """
        fy = await resolve_fiscal_year(self._client, exercice)

        ledger_account_id: int | None = None
        if account_number is not None:
            ledger_account_id = await self._resolve_account_number(account_number)

        # Normalisation des bornes (str ISO ou date → str ISO).
        effective_date_from = (
            date_from.isoformat()
            if isinstance(date_from, date)
            else date_from
            if isinstance(date_from, str)
            else fy.start
        )
        effective_date_to = (
            date_to.isoformat()
            if isinstance(date_to, date)
            else date_to
            if isinstance(date_to, str)
            else fy.finish
        )

        params = self._build_filter_params(
            effective_date_from=effective_date_from,
            effective_date_to=effective_date_to,
            ledger_account_id=ledger_account_id,
            journal_id=journal_id,
        )
        if cursor is not None:
            params["cursor"] = cursor

        raw_lines, truncated, next_cursor = await paginate_with_cap(
            self._client,
            "/ledger_entry_lines",
            params=params,
            data_key="items",
            cap=cap,
        )

        lines = [self._transform_line(item) for item in raw_lines]

        summary: LedgerSummary | None = None
        summary_unavailable_reason: str | None = None
        if truncated:
            summary_unavailable_reason = "truncated"
        else:
            total_debit = sum((line.debit for line in lines), Decimal("0"))
            total_credit = sum((line.credit for line in lines), Decimal("0"))
            summary = LedgerSummary(
                total_debit=total_debit.quantize(_Q),
                total_credit=total_credit.quantize(_Q),
                balance=(total_debit - total_credit).quantize(_Q),
            )

        return LedgerEntriesPage(
            lines=lines,
            truncated=truncated,
            next_cursor=next_cursor,
            exercice_resolved=fy.model_dump(),
            filters_applied={
                "account_number": account_number,
                "date_from": effective_date_from,
                "date_to": effective_date_to,
                "journal_id": journal_id,
            },
            count=len(lines),
            summary=summary,
            summary_unavailable_reason=summary_unavailable_reason,
        )

    async def iterate(
        self,
        exercice: str,
        *,
        account_number: str | None = None,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        journal_id: int | None = None,
    ) -> AsyncIterator[LedgerEntryLine]:
        """Itère sur toutes les lignes (sans cap dur)."""
        fy = await resolve_fiscal_year(self._client, exercice)
        ledger_account_id: int | None = None
        if account_number is not None:
            ledger_account_id = await self._resolve_account_number(account_number)

        effective_date_from = (
            date_from.isoformat()
            if isinstance(date_from, date)
            else date_from
            if isinstance(date_from, str)
            else fy.start
        )
        effective_date_to = (
            date_to.isoformat()
            if isinstance(date_to, date)
            else date_to
            if isinstance(date_to, str)
            else fy.finish
        )

        params = self._build_filter_params(
            effective_date_from=effective_date_from,
            effective_date_to=effective_date_to,
            ledger_account_id=ledger_account_id,
            journal_id=journal_id,
        )

        async for item in paginate(
            self._client, "/ledger_entry_lines", params=params, data_key="items"
        ):
            yield self._transform_line(item)

    async def list(
        self,
        exercice: str,
        *,
        account_number: str | None = None,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        journal_id: int | None = None,
    ) -> list[LedgerEntryLine]:
        """Renvoie toutes les lignes sous forme de liste (matérialise l'itération)."""
        return [
            line
            async for line in self.iterate(
                exercice,
                account_number=account_number,
                date_from=date_from,
                date_to=date_to,
                journal_id=journal_id,
            )
        ]

    # -----------------------------------------------------------------------
    # Module write — create() : balance + WAL + read-back IN_DOUBT
    # -----------------------------------------------------------------------

    @staticmethod
    def _quantize(amount: Decimal) -> Decimal:
        """Quantize à 2 décimales en ROUND_HALF_UP (usage fiscal FR,)."""
        return amount.quantize(_Q, rounding=ROUND_HALF_UP)

    def _assert_balanced(self, lines: Sequence[NewLedgerEntryLine]) -> None:
        """Équilibre Σdébit == Σcrédit : quantize ROUND_HALF_UP puis égalité exacte sur
        Decimals quantizés (ADR). Lève `UnbalancedEntryError` AVANT tout POST."""
        total_debit = self._quantize(sum((ln.debit for ln in lines), Decimal("0")))
        total_credit = self._quantize(sum((ln.credit for ln in lines), Decimal("0")))
        if total_debit != total_credit:
            raise UnbalancedEntryError(total_debit, total_credit)

    @staticmethod
    def _entry_signature(
        lines: Sequence[NewLedgerEntryLine],
    ) -> tuple[tuple[int, str, str, str], ...]:
        """Signature multiset RÉ-OBSERVABLE des lignes : tuple trié de
        (account_id, débit quantizé str, crédit str, libellé ligne). Le libellé de ligne EST
        ré-observable au DÉTAIL → inclus pour que la clé read-back soit aussi fine que le
        fingerprint sur les champs ré-observables. `piece_number` (auto Pennylane, EC-2) et
        `currency` (jamais renvoyé en réponse, spec OpenAPI) sont EXCLUS de la clé read-back."""
        return tuple(
            sorted(
                (
                    int(ln.ledger_account_id),
                    str(ln.debit.quantize(_Q, rounding=ROUND_HALF_UP)),
                    str(ln.credit.quantize(_Q, rounding=ROUND_HALF_UP)),
                    ln.label or "",
                )
                for ln in lines
            )
        )

    @staticmethod
    def _candidate_signature(
        lines: Sequence[CreatedLedgerEntryLine],
    ) -> tuple[tuple[int, str, str, str], ...] | None:
        """Signature multiset d'un candidat (lignes du DÉTAIL). Le compte vient de
        `ledger_account.id` imbriqué (`resolved_account_id`, RB-3) ; le libellé de ligne du
        DÉTAIL est inclus (symétrie `_entry_signature`). Si UNE ligne n'a pas de compte
        résoluble → `None` : le candidat est écarté (jamais un faux `0`)."""
        sig: list[tuple[int, str, str, str]] = []
        for ln in lines:
            account_id = ln.resolved_account_id
            if account_id is None:
                return None  # candidat non fiable → écarté
            sig.append(
                (
                    account_id,
                    str(ln.debit.quantize(_Q, rounding=ROUND_HALF_UP)),
                    str(ln.credit.quantize(_Q, rounding=ROUND_HALF_UP)),
                    ln.label or "",
                )
            )
        return tuple(sorted(sig))

    async def _fetch_detail(self, entry_id: int) -> CreatedLedgerEntry:
        """`GET /ledger_entries/{id}` → écriture complète AVEC lignes (la LISTE n'en a
        PAS, RB-1). Lève en cas d'échec (404 pas-encore-visible, 403, 5xx) → l'appelant
        bascule IN_DOUBT (ne jamais conclure 0-match sur un détail non vérifiable)."""
        resp = await self._client.request("GET", f"/ledger_entries/{entry_id}")
        return CreatedLedgerEntry.model_validate(resp)

    async def _memorized_id_still_exists(self, entry_id: int) -> bool | None:
        """Vérification d'existence au court-circuit (edge delete↔WAL, validation réelle du 2026-06-10).

        Un 404 isolé n'est PAS un signal de suppression fiable : il peut être TRANSITOIRE
        (lag read-after-write Pennylane — cf. `_fetch_detail` « 404 pas-encore-visible »),
        et le chemin le plus exposé est NOMINAL (re-soumission idempotente rapide post-201,
        retry transport de l'écran). Conclure « supprimée » sur un seul 404 invaliderait un
        mapping VIVANT puis re-POSTerait → doublon silencieux.

        Politique D1 : ``False`` (supprimée) EXIGE **2× 404 consécutifs espacés de
        ``RECONCILE_GRACE_SECONDS``** (le même délai de grâce que la réconciliation).
        ``True`` = l'écriture existe (1er GET 200, ou 404 puis 200 au re-GET = lag confirmé
        transitoire) ; ``None`` = invérifiable (5xx / timeout / auth / corps illisible, sur
        l'un ou l'autre GET) → l'appelant reste CONSERVATEUR (id mémorisé renvoyé tel quel).
        On ne RÉ-ÉCRIT jamais sur un doute."""
        first = await self._probe_detail_404(entry_id)
        if first is not False:
            return first  # True (vivante) ou None (invérifiable) → pas d'invalidation
        # 1er 404 : possiblement transitoire → grâce DÉDIÉE puis SECOND GET.
        await asyncio.sleep(DELETE_PROBE_GRACE_SECONDS)
        second = await self._probe_detail_404(entry_id)
        if second is False:
            return False  # 2× 404 espacés → suppression confirmée
        # 200 au re-GET (lag transitoire confirmé) → vivante ; invérifiable → conservateur.
        return True if second is True else None

    async def _probe_detail_404(self, entry_id: int) -> bool | None:
        """Un GET détail : ``True`` existe · ``False`` 404 · ``None`` invérifiable."""
        try:
            await self._fetch_detail(entry_id)
            return True
        except PennylaneClientError as exc:
            return False if exc.status_code == 404 else None
        except (
            PennylaneError,
            httpx.TransportError,
            httpx.DecodingError,
            json.JSONDecodeError,
            ValidationError,
        ):
            return None

    @staticmethod
    def _date_window(date_str: str) -> tuple[str, str]:
        """Plage J-1..J+1 (anti normalisation timezone / date d'effet côté Pennylane,
        #4) : un filtre date `eq` exact pourrait exclure l'écriture si l'API la range à
        une date voisine → faux 0-match → re-POST."""
        d = date.fromisoformat(date_str)
        return (d - timedelta(days=1)).isoformat(), (d + timedelta(days=1)).isoformat()

    async def _fetch_id_watermark(self) -> int:
        """Repère d'identifiant SERVEUR capturé AVANT le POST (bug B, revue v2) :
        ``GET /ledger_entries?sort=-id&limit=1`` → id max courant. Toute écriture d'``id >
        id_watermark`` au read-back est forcément la nôtre — robuste au skew d'horloge
        (aucune comparaison de timestamps). Ledger vide → ``0``."""
        try:
            resp = await self._client.request(
                "GET", "/ledger_entries", params={"sort": "-id", "limit": 1}
            )
            items = resp.get("items") or []
            if not items:
                return 0
            top = items[0].get("id")
            return int(top) if top is not None else 0
        except (json.JSONDecodeError, ValueError, TypeError, httpx.DecodingError) as exc:
            # HERM-4 : fail-closed pré-POST () — repère serveur illisible (corps non-JSON,
            # id non entier) → PennylaneServerError typée, jamais d'exception nue. create()
            # la propage → AUCUN POST sans repère valide. (Les PennylaneError de request() —
            # 4xx/5xx typés — ne sont PAS attrapées ici : elles propagent telles quelles.)
            raise PennylaneServerError(
                "watermark non concluant : réponse /ledger_entries illisible"
            ) from exc

    async def _read_back(
        self,
        *,
        date: str,
        journal_id: int,
        label: str,
        due_date: str | None,
        target_signature: tuple[tuple[int, str, str, str], ...],
        id_watermark: int,
    ) -> tuple[Sequence[CreatedLedgerEntry], bool]:
        """Read-back 2 temps (ADR, RB-1) — PUREMENT INFORMATIONNEL en v4 (b-ciblé) : il
        NE confirme JAMAIS, il remonte les candidats EMPREINTE-IDENTIQUE à présenter à
        l'humain. La LISTE `/ledger_entries` ne renvoie PAS les lignes → filtre serveur
        `date` (plage J-1..J+1) + `journal_id` (les SEULS champs filtrables), PAGINE, et pour
        chaque candidat d'`id > id_watermark` fait `GET /ledger_entries/{id}` (DÉTAIL) → match
        sur la clé RÉ-OBSERVABLE *aussi fine que le fingerprint* : `date` exacte + `label`
        (entrée) + `due_date` + signature de lignes (compte imbriqué RB-3 + libellé ligne).
        `piece_number`/`currency` exclus (non contrôlés / non ré-observables).

        Résilience par candidat (medium 3) : un échec de DÉTAIL (404/403/5xx/illisible) saute
        CE candidat ; `had_failure` remonte (informatif — la décision reste IN_DOUBT).

        Retourne `(candidats, had_failure)`."""
        date_from, date_to = self._date_window(date)
        filter_clauses = [
            {"field": "date", "operator": "gteq", "value": date_from},
            {"field": "date", "operator": "lteq", "value": date_to},
            {"field": "journal_id", "operator": "eq", "value": journal_id},
        ]
        params = {"filter": json.dumps(filter_clauses), "limit": DEFAULT_PAGE_LIMIT}
        wanted_label = label or ""
        wanted_due = due_date or None
        matched: list[CreatedLedgerEntry] = []
        had_failure = False
        async for item in paginate(
            self._client, "/ledger_entries", params=params, data_key="items"
        ):
            item_id = item.get("id")
            if item_id is None or int(item_id) <= id_watermark:
                continue  # créé avant notre repère (id <= watermark) → pas notre écriture
            # Pré-filtre ré-observable depuis la LISTE (date exacte + label + due_date) avant
            # le coûteux GET DÉTAIL ; la signature de lignes (DÉTAIL) tranche ensuite.
            if (
                item.get("date") != date
                or (item.get("label") or "") != wanted_label
                or (item.get("due_date") or None) != wanted_due
            ):
                continue
            try:
                detail = await self._fetch_detail(int(item_id))
            except (
                PennylaneError,
                httpx.TransportError,
                httpx.DecodingError,
                json.JSONDecodeError,
                ValidationError,
            ):
                had_failure = True  # candidat non vérifiable → sauté (medium 3)
                continue
            sig = self._candidate_signature(detail.ledger_entry_lines)
            if (
                sig is not None
                and sig == target_signature
                and (detail.label or "") == wanted_label
                and (detail.due_date or None) == wanted_due
                and detail.date == date
            ):
                matched.append(detail)
        return matched, had_failure

    async def _reconcile(
        self,
        *,
        date: str,
        journal_id: int,
        label: str,
        due_date: str | None,
        target_signature: tuple[tuple[int, str, str, str], ...],
        id_watermark: int,
    ) -> tuple[Sequence[CreatedLedgerEntry], bool]:
        """Réconciliation INFORMATIONNELLE post-POST-incertain (ADR, v4 b-ciblé) : délai
        de GRÂCE (laisse un POST en vol se poser, atténue le lag read-your-writes) → read-back
        → liste des candidats empreinte-identique à présenter à l'humain. NE décide PAS : la
        décision est TOUJOURS IN_DOUBT (le read-back ne confirme jamais). Retourne
        `(candidats, had_failure)`."""
        await asyncio.sleep(RECONCILE_GRACE_SECONDS)
        return await self._read_back(
            date=date,
            journal_id=journal_id,
            label=label,
            due_date=due_date,
            target_signature=target_signature,
            id_watermark=id_watermark,
        )

    async def create(
        self,
        *,
        date: str,
        label: str,
        journal_id: int,
        lines: Sequence[NewLedgerEntryLine],
        namespace: str,
        due_date: str | None = None,
        currency: str | None = None,
    ) -> CreatedLedgerEntry:
        """Crée une écriture (`POST /ledger_entries`) avec garantie anti-doublon (WAL, read-back ciblé par signature).

        `namespace` = clé de tenancy opaque (p. ex. `dossier_id`) scopant le WAL. Montants
        quantizés ROUND_HALF_UP. `currency` TOUJOURS envoyée si fournie (justesse
        multi-devises) ; `piece_number` n'est PAS envoyé (auto Pennylane, EC-2).

        Flux : équilibre pré-POST (`UnbalancedEntryError`) → repère d'identifiant serveur
        pré-POST (`id_watermark`, **fail-closed** : pas de POST sans repère) → `WALManager.
        reserve` (court-circuit CONFIRMED zéro-POST ; refus DUPLICATE ; IN_FLIGHT + clé
        read-back + watermark durables AVANT le POST) → **UN SEUL POST** (le write-retryer
        interne ne retente que `ConnectError`/`429`, octets jamais envoyés) :
          - **201 propre** (accusé de réception autoritative de l'unique POST) → `CONFIRMED` ;
          - 4xx / auth / 429-épuisé / `ConnectError` (rien atterri) → `FAILED`, relève ;
          - 5xx / timeout post-envoi / 201 illisible (INCERTAIN) → read-back INFORMATIONNEL →
            **toujours `IN_DOUBT`** : le read-back ne confirme JAMAIS (règle (b) ciblée — un
            candidat empreinte-identique est le territoire doublon-métier réel → l'humain
            tranche, jamais d'auto-confirm silencieux). `WALInDoubtError` porte les candidats.

        **Edge delete↔WAL (validation réelle du 2026-06-10)** :
        au court-circuit, l'existence de l'id mémorisé est VÉRIFIÉE — l'écriture a pu être
        supprimée via l'UI Pennylane (l'API V2 n'expose pas de `DELETE /ledger_entries`,
        spec + sonde). La suppression n'est conclue que sur **2× 404 espacés de la grâce
        DÉDIÉE `DELETE_PROBE_GRACE_SECONDS`** (un 404 isolé peut être un lag read-after-write
        TRANSITOIRE — chemin nominal de la re-soumission idempotente rapide). Ensuite,
        **garde d'âge RES-1** : écriture confirmée il y a moins de
        `DEAD_ID_AGE_GUARD_SECONDS` → probablement vivante (lag) → bascule **GARDÉE**
        `CONFIRMED→IN_DOUBT` (conditionnelle par identité) + `WALInDoubtError` (l'humain
        tranche via `resolve()`) — JAMAIS de ré-écriture automatique. Mapping ANCIEN →
        invalidation conditionnelle par IDENTITÉ atomique store-level
        (`transition_confirmed_if` WHERE CONFIRMED AND pennylane_id=dead_id) + rejeu UNE
        fois ; sous concurrence, le perdant ne peut pas écraser un mapping re-confirmé
        vivant — il re-tente et court-circuite sur le mapping FRAIS. Toute autre
        défaillance de la vérification → conservateur (id mémorisé renvoyé). Épuisement → bascule GARDÉE `CONFIRMED→IN_DOUBT` + `WALInDoubtError` portant le
        dead_id en candidat (`resolve()` utilisable).
        """
        last_dead: _DeadConfirmedIdError | None = None
        for attempt in (1, 2):
            try:
                return await self._create_with_wal(
                    date=date,
                    label=label,
                    journal_id=journal_id,
                    lines=lines,
                    namespace=namespace,
                    due_date=due_date,
                    currency=currency,
                )
            except _DeadConfirmedIdError as exc:
                last_dead = exc
                wal = self._get_wal()
                if attempt == 2:
                    # RES-2 : épuisement (courts-circuits morts répétés = concurrence
                    # pathologique) → bascule GARDÉE CONFIRMED→IN_DOUBT (conditionnelle
                    # par identité — jamais d'écrasement aveugle) puis réconciliation
                    # humaine via resolve(). PAS d'invalidation sur la dernière itération.
                    await wal.doubt_confirmed(
                        namespace,
                        exc.fingerprint,
                        expected_pennylane_id=exc.dead_id,
                        reason=(
                            f"courts-circuits répétés sur id introuvable {exc.dead_id} "
                            "(2× 404) — concurrence anormale, réconciliation humaine"
                        ),
                    )
                    break
                if self._dead_id_is_young(exc.confirmed_at):
                    # RES-1 garde d'âge : confirmée RÉCEMMENT → un double-404 est suspect
                    # (lag read-after-write : l'écriture est PROBABLEMENT vivante).
                    # IN_DOUBT GARDÉ (l'humain tranche via resolve : landed=true + id si
                    # elle existe, landed=false → ré-écriture) — JAMAIS d'invalidation +
                    # ré-écriture automatiques sur une écriture potentiellement vivante.
                    moved = await wal.doubt_confirmed(
                        namespace,
                        exc.fingerprint,
                        expected_pennylane_id=exc.dead_id,
                        reason=(
                            f"2× 404 sur l'écriture {exc.dead_id} confirmée récemment — "
                            "lag read-after-write possible, vérification humaine"
                        ),
                    )
                    if not moved:
                        continue  # mapping changé (concurrent) → re-réserver (mapping frais)
                    break
                # Mapping ANCIEN (longuement visible) + 2× 404 espacés = suppression réelle
                # (UI). Invalidation CONDITIONNELLE par identité (D2) : True = FAILED, la
                # passe suivante RÉ-ÉCRIT ; False = mapping changé (concurrent) → la passe
                # suivante court-circuite sur le mapping FRAIS. Jamais d'écrasement aveugle.
                await wal.invalidate_confirmed(
                    namespace,
                    exc.fingerprint,
                    expected_pennylane_id=exc.dead_id,
                    reason=(
                        f"écriture {exc.dead_id} supprimée côté Pennylane (2× 404 espacés "
                        "au court-circuit, mapping ancien) — invalidé, ré-écriture autorisée"
                    ),
                )
                continue
        assert last_dead is not None
        raise WALInDoubtError(
            fingerprint=last_dead.fingerprint,
            candidates=[last_dead.dead_id],
            reason=(
                "id mémorisé introuvable (2× 404 espacés) — réconciliation humaine : "
                "resolve(landed=true, id) si l'écriture existe chez Pennylane, "
                "resolve(landed=false) pour autoriser la ré-écriture"
            ),
        ) from last_dead

    @staticmethod
    def _dead_id_is_young(confirmed_at: str) -> bool:
        """Garde d'âge RES-1 : ``True`` si le confirm mémorisé date de moins de
        ``DEAD_ID_AGE_GUARD_SECONDS``. Horodatage illisible → ``True`` (CONSERVATEUR :
        dans le doute, traiter comme jeune → IN_DOUBT humain, jamais de ré-écriture)."""
        try:
            confirmed = datetime.fromisoformat(confirmed_at)
        except (ValueError, TypeError):
            return True
        if confirmed.tzinfo is None:
            confirmed = confirmed.replace(tzinfo=UTC)
        age = (datetime.now(tz=UTC) - confirmed).total_seconds()
        return age < DEAD_ID_AGE_GUARD_SECONDS

    async def _create_with_wal(
        self,
        *,
        date: str,
        label: str,
        journal_id: int,
        lines: Sequence[NewLedgerEntryLine],
        namespace: str,
        due_date: str | None = None,
        currency: str | None = None,
    ) -> CreatedLedgerEntry:
        """Une passe de création (garde STORE-1 → équilibre → watermark → reserve → POST).
        Lève `_DeadConfirmedIdError` si le court-circuit pointe un id supprimé (404 propre) —
        `create()` invalide alors le mapping et rejoue UNE fois (watermark re-capturé)."""
        # STORE-1 — garde non-omissible AVANT tout effet : un write
        # réel ne DOIT JAMAIS être adossé à un NullStore (no-op) ni à un store absent, sinon
        # AUCUNE garantie anti-doublon (chaque appel ré-écrirait). Le NullStore reste réservé
        # à la lecture seule / aux tests. Refus fail-fast (le câblage lifespan injecte le store
        # réel — SqliteIdempotencyStore mono-machine, PostgresStore cross-host — AVANT le write).
        if self._wal_store is None or isinstance(self._wal_store, NullStore):
            raise WALStoreRequiredError()
        quantized = [
            NewLedgerEntryLine(
                ledger_account_id=ln.ledger_account_id,
                debit=self._quantize(ln.debit),
                credit=self._quantize(ln.credit),
                label=ln.label,
            )
            for ln in lines
        ]
        entry = NewLedgerEntry(
            date=date,
            label=label,
            journal_id=journal_id,
            ledger_entry_lines=quantized,
            due_date=due_date,
            currency=currency,
        )
        self._assert_balanced(entry.ledger_entry_lines)
        # Payload POST = base du fingerprint d'idempotence (WAL dedup) : inclut `currency` si
        # fournie (€100 ≠ $100, jamais faussement dédupliqués) ; `piece_number` absent (auto).
        payload = entry.model_dump(mode="json", exclude_none=True)

        # Clé de read-back = sous-ensemble RÉ-OBSERVABLE du fingerprint (currency exclue : non
        # renvoyée par l'API — spec OpenAPI ; piece_number exclu : auto). Persistée AVANT le
        # POST (recover()).
        target_signature = self._entry_signature(entry.ledger_entry_lines)
        readback_key = {
            "date": entry.date,
            "journal_id": entry.journal_id,
            "label": entry.label,
            "due_date": entry.due_date,
            "line_signature": [list(t) for t in target_signature],
        }
        # Repère d'identifiant serveur AVANT le POST (fail-closed : lève si lecture non
        # concluante → aucun POST sans repère).
        id_watermark = await self._fetch_id_watermark()

        wal = self._get_wal()
        async with wal.reserve(
            namespace=namespace,
            payload=payload,
            readback_key=readback_key,
            id_watermark=id_watermark,
        ) as resv:
            if resv.should_short_circuit:
                # Déjà CONFIRMED (replay même fingerprint) → id mémorisé, ZÉRO POST.
                memorized = resv.memorized_id
                if memorized is None:
                    raise WALInDoubtError(
                        fingerprint=resv.fingerprint,
                        candidates=[],
                        reason="CONFIRMED sans pennylane_id mémorisé (état incohérent)",
                    )
                # Edge delete↔WAL (validation réelle du 2026-06-10) : l'écriture mémorisée a pu être
                # SUPPRIMÉE via l'UI Pennylane → vérification d'existence. 404 PROPRE →
                # `_DeadConfirmedIdError` (create() invalide + rejoue UNE fois) ; True ou
                # invérifiable (None) → conservateur, id mémorisé renvoyé (comportement
                # antérieur — jamais de ré-écriture sur un doute).
                exists = await self._memorized_id_still_exists(int(memorized))
                if exists is False:
                    raise _DeadConfirmedIdError(
                        resv.fingerprint, int(memorized), resv.memorized_updated_at
                    )
                return CreatedLedgerEntry(id=int(memorized))
            if resv.is_duplicate_in_progress:
                raise WALDuplicatePayloadError(
                    fingerprint=resv.fingerprint, wal_state=resv.duplicate_state
                )
            # PROCEEDING : IN_FLIGHT + clé read-back durables. UN SEUL POST (pas de boucle
            # re-POST : tout doute → IN_DOUBT ; le seul ré-essai sûr — ConnectError/429, rien
            # envoyé — est interne au write-retryer).
            try:
                raw = await self._client.request_write("POST", "/ledger_entries", json=payload)
                created = CreatedLedgerEntry.model_validate(raw)
            except (
                PennylaneClientError,
                PennylaneAuthError,
                PennylaneRateLimitError,
            ) as exc:
                # 4xx (dont 422) / 401-403 / 429-épuisé : rien atterri → FAILED.
                await resv.fail(str(exc))
                raise
            except httpx.ConnectError as exc:
                # Connexion JAMAIS établie → octets jamais envoyés → rien atterri → FAILED.
                await resv.fail(f"connexion non établie : {exc!r}")
                raise
            except (
                PennylaneServerError,
                httpx.TransportError,
                httpx.DecodingError,
                json.JSONDecodeError,
                ValidationError,
            ) as exc:
                # État INCERTAIN (5xx / ReadTimeout / ConnectTimeout / 201 illisible / 201 sans
                # id) → l'écriture a PU atterrir. Read-back INFORMATIONNEL (jamais confirm) →
                # IN_DOUBT, candidats empreinte-identique présentés à l'humain.
                candidates: list[int] = []
                try:
                    matches, _had_failure = await self._reconcile(
                        date=entry.date,
                        journal_id=entry.journal_id,
                        label=entry.label,
                        due_date=entry.due_date,
                        target_signature=target_signature,
                        id_watermark=id_watermark,
                    )
                    candidates = [int(m.id) for m in matches]
                except (
                    PennylaneError,
                    httpx.TransportError,
                    httpx.DecodingError,
                    ValidationError,
                    json.JSONDecodeError,
                    TypeError,
                    ValueError,
                ):
                    # Read-back indisponible → candidats inconnus ; IN_DOUBT quand même
                    # (jamais d'exception hors-contrat, medium 2 / CI).
                    candidates = []
                await resv.mark_in_doubt(repr(exc))
                raise WALInDoubtError(
                    fingerprint=resv.fingerprint,
                    candidates=candidates,
                    reason=(
                        "POST incertain (timeout/5xx/illisible) — le read-back ne confirme "
                        "jamais ; réconciliation humaine (vérifier chez Pennylane)"
                    ),
                ) from exc
            else:
                # 201 PROPRE = accusé de réception autoritative de NOTRE unique POST → confirm.
                landed_id = int(created.id)
                try:
                    await resv.confirm(landed_id)
                except WALStoreError as exc:
                    # Garde store (UNIQUE(ns,pennylane_id) WHERE CONFIRMED → id déjà confirmé
                    # sous un autre fingerprint, OU store indisponible) → IN_DOUBT, jamais de
                    # confirm silencieux ni de doublon.
                    raise WALInDoubtError(
                        fingerprint=resv.fingerprint,
                        candidates=[landed_id],
                        reason=(
                            "confirmation 201 refusée par le store (id déjà confirmé ailleurs "
                            "ou store indisponible) — réconciliation humaine"
                        ),
                    ) from exc
                return created

    async def recover(self, namespace: str | None = None) -> Sequence[dict[str, Any]]:
        """Reprise au démarrage : pour chaque
        entrée **IN_FLIGHT** (crash en vol — les `IN_DOUBT` sont laissés à l'humain, REC-2),
        un read-back INFORMATIONNEL présente les candidats empreinte-identique, puis l'entrée
        bascule **IN_DOUBT** (résolution humaine via `WALManager.resolve`). Le read-back ne
        confirme JAMAIS (cohérent `create()`). JAMAIS de re-POST en reprise.

        ``namespace`` (multi-dossiers, R1 2026-06-10) : si fourni, SEULES les entrées de ce
        namespace sont reprises — indispensable quand chaque dossier a SON token/client
        (le read-back d'un dossier X avec le client du dossier Y serait mensonger) ; la
        couche appelante boucle alors sur ses dossiers, chacun avec son client.

        **RE-ENTRANT** : seules les entrées IN_FLIGHT sont traitées (filtre `:696`) ; après une
        passe l'entrée est IN_DOUBT donc ignorée au tour suivant — relançable sans double
        transition. Corps par entrée **HERMÉTIQUE** : clé de read-back malformée,
        erreur driver du store ou read-back indisponible n'avortent jamais la reprise des
        suivantes ni ne font fuir d'exception nue. Retourne un rapport par entrée."""
        wal = self._get_wal()
        report: list[dict[str, Any]] = []
        for e in await wal.iter_nonterminal():
            if namespace is not None and e.namespace != namespace:
                continue  # hors du dossier demandé → repris par SON client (multi-dossiers)
            if e.state is not WALState.IN_FLIGHT:
                continue  # IN_DOUBT déjà flaggé humain → ne pas auto-résoudre (REC-2)
            try:
                rb = e.readback_key
                if rb is None or e.id_watermark is None:
                    await wal.recover_in_doubt(
                        e.namespace, e.fingerprint, "reprise : clé de read-back absente"
                    )
                    report.append(
                        {"fingerprint": e.fingerprint, "action": "in_doubt_no_readback_key"}
                    )
                    continue
                # NBI-1 : parse de la clé persistée DANS un try — une clé présente-mais-malformée
                # (torn-write, schéma v3→v4, corruption) → IN_DOUBT, jamais de KeyError/IndexError
                # nu qui avorterait la reprise et briquerait le boot.
                try:
                    target_signature = tuple(
                        (int(s[0]), str(s[1]), str(s[2]), str(s[3])) for s in rb["line_signature"]
                    )
                    rb_date = str(rb["date"])
                    rb_journal = int(rb["journal_id"])
                except (KeyError, IndexError, TypeError, ValueError):
                    await wal.recover_in_doubt(
                        e.namespace, e.fingerprint, "reprise : readback_key malformé"
                    )
                    report.append(
                        {"fingerprint": e.fingerprint, "action": "in_doubt_malformed_readback"}
                    )
                    continue
                candidates: list[int] = []
                try:
                    matches, _had_failure = await self._read_back(
                        date=rb_date,
                        journal_id=rb_journal,
                        label=str(rb.get("label") or ""),
                        due_date=rb.get("due_date"),
                        target_signature=target_signature,
                        id_watermark=int(e.id_watermark),
                    )
                    candidates = [int(m.id) for m in matches]
                except (
                    PennylaneError,
                    httpx.TransportError,
                    httpx.DecodingError,
                    ValidationError,
                    json.JSONDecodeError,
                    TypeError,
                    ValueError,
                ):
                    candidates = []  # read-back indisponible → IN_DOUBT quand même
                await wal.recover_in_doubt(
                    e.namespace,
                    e.fingerprint,
                    f"reprise : POST incertain au crash — réconciliation humaine "
                    f"({len(candidates)} candidat(s))",
                )
                report.append(
                    {
                        "fingerprint": e.fingerprint,
                        "action": "in_doubt",
                        "candidates": candidates,
                    }
                )
            except WALStoreError as exc:
                # Erreur store sur la transition → on note et on CONTINUE (hermétique, CI-1) ;
                # l'entrée reste IN_FLIGHT → re-tentée à la prochaine reprise (ré-entrant).
                report.append(
                    {"fingerprint": e.fingerprint, "action": "store_error", "error": repr(exc)}
                )
                continue
        return report
