"""Resource façade write — rapprochement de paiement + statut facture.

Deux opérations d'écriture adossées au WAL anti-doublon, distinctes de
`LedgerEntriesResource.create` par leur profil de réponse : ces endpoints renvoient
``204 No Content`` SANS corps (aucun ``pennylane_id``). Le patron `create()` est
ADAPTÉ — pas copié :

  - pas de ``id_watermark`` ni de read-back par signature (rien n'est « créé » à
    retrouver par contenu) ;
  - ``confirm(None)`` (CONFIRMED sans id serveur — l'index UNIQUE partiel SQLite
    autorise plusieurs NULL) ;
  - la garantie anti-doublon repose ENTIÈREMENT sur le couple ``(namespace,
    fingerprint)`` : un re-appel de la MÊME opération court-circuite (zéro POST).

Distinction des issues PRÉSERVÉE (sécurité write,/) :
  - 204                        → CONFIRMED ;
  - 4xx/422/auth/429-épuisé/ConnectError (rien atterri) → FAILED, relève ;
  - 5xx/timeout/illisible (INCERTAIN)                   → IN_DOUBT (jamais de
    re-soumission auto — résolution humaine via ``WALManager.resolve``).

Endpoints (spec OpenAPI v2.0, captures réelles 2026-06-19) :
  - ``POST /supplier_invoices/{id}/matched_transactions``  body ``{transaction_id}``
  - ``POST /customer_invoices/{id}/matched_transactions``  body ``{transaction_id}``
  - ``PUT  /supplier_invoices/{id}/payment_status``        body ``{payment_status}``
  - ``PUT  /customer_invoices/{id}/mark_as_paid``          (sans corps)

Sonde d'état au court-circuit (F-1, revue adversariale 2026-08-28). Un mapping CONFIRMED
atteste qu'une opération a été ENVOYÉE, pas que l'état courant lui correspond encore :
une facture passée « payée » ici puis remise « à payer » dans Pennylane, redemandée
« payée », recevait un « déjà fait » faux. Pour les deux ``PUT`` de statut, le
court-circuit relit d'abord la facture :
  - état observé conforme au statut demandé → ``already_confirmed`` (``state_verified``) ;
  - état observé CONTRAIRE (flip-flop avéré) → le mapping sans id est invalidé
    (``CONFIRMED → FAILED``, identité = absence d'id) et le ``PUT`` est renvoyé UNE fois
    (un ``PUT`` de statut est rejouable sans doublon, contrairement à un ``POST``) ;
  - état observé AMBIGU (paiement en cours, partiel, erreur…) ou relecture en échec →
    aucun envoi, ``already_confirmed`` NON vérifié : vérification humaine.
Correspondance mesurée sur le bac à sable le 2026-08-29 : ``PUT paid`` → lecture
``payment_status="paid_offline"`` ; ``PUT to_be_paid`` → ``"to_be_paid"`` ; état initial
d'une facture non traitée : ``"to_be_processed"``. Un rapprochement bancaire complet
laisse ``payment_status`` inchangé mais passe ``paid`` à true et le reste à payer à 0 :
``paid=true`` et ``fully_paid`` sont considérés conformes à « payée » sans renvoi. Le
rapprochement (``match_payment``, un ``POST``) garde le « déjà fait » historique,
documenté tel quel.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import ValidationError

from ..client import PennylaneClient
from ..exceptions import (
    PennylaneAuthError,
    PennylaneClientError,
    PennylaneError,
    PennylaneRateLimitError,
    PennylaneServerError,
)
from ..models.invoice_writes import InvoiceKind, InvoiceWriteAck, SupplierPaymentStatus
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

# ─── Sonde d'état F-1 ────────────────────────────────────────────────────────────────

# Lectures `payment_status` (GET /supplier_invoices/{id}) conformes au statut DEMANDÉ.
_SUPPLIER_SATISFIED: dict[str, frozenset[str]] = {
    "paid": frozenset({"paid_offline", "fully_paid"}),
    "to_be_paid": frozenset({"to_be_paid"}),
}
# Lectures CONTRAIRES au statut demandé (flip-flop avéré → renvoi du PUT).
_SUPPLIER_OPPOSITE: dict[str, frozenset[str]] = {
    "paid": frozenset({"to_be_paid", "to_be_processed"}),
    "to_be_paid": frozenset({"paid_offline", "fully_paid", "to_be_processed"}),
}
# Toute autre lecture (partially_paid, payment_error, payment_scheduled, payment_in_progress,
# payment_emitted, payment_found…) est AMBIGUË : aucun envoi automatique.

_Verdict = Literal["match", "mismatch", "ambiguous", "unverifiable"]


@dataclass(frozen=True)
class _StateVerdict:
    verdict: _Verdict
    observed: str | None


class _StaleConfirmedStateError(Exception):
    """Interne : le court-circuit CONFIRMED pointe un état périmé (sonde = mismatch)."""

    def __init__(self, fingerprint: str, observed: str | None) -> None:
        super().__init__(fingerprint)
        self.fingerprint = fingerprint
        self.observed = observed


class InvoicePaymentsResource:
    """Façade write : rapprochement de transaction + mise à jour de statut de paiement.

    `wal_store` (injecté par la couche applicative) DOIT être un store réel en
    production — un write adossé à `NullStore`/absent lève `WALStoreRequiredError`
    (STORE-1, aucune garantie anti-doublon sinon). `WALManager` est construit
    paresseusement au 1er write (l'usage lecture seule ne déclenche aucun warning).
    """

    def __init__(
        self, client: PennylaneClient, *, wal_store: IdempotencyStore | None = None
    ) -> None:
        self._client = client
        self._wal_store = wal_store
        self._wal: WALManager | None = None

    def _get_wal(self) -> WALManager:
        """WALManager paresseux + mémoïsé (verrous (ns,fp) persistants entre writes)."""
        if self._wal is None:
            self._wal = WALManager(self._wal_store or NullStore())
        return self._wal

    async def _probe_supplier_status(
        self, invoice_id: int, requested: SupplierPaymentStatus
    ) -> _StateVerdict:
        """Relit ``payment_status`` de la facture fournisseur et le confronte au demandé."""
        try:
            raw = await self._client.request("GET", f"/supplier_invoices/{invoice_id}")
        except (PennylaneError, httpx.HTTPError, ValueError):
            return _StateVerdict("unverifiable", None)
        observed = raw.get("payment_status")
        if not isinstance(observed, str):
            return _StateVerdict("unverifiable", None)
        if observed in _SUPPLIER_SATISFIED[requested]:
            return _StateVerdict("match", observed)
        # ``paid=true`` = réglée par rapprochement bancaire complet (validation réelle du
        # 2026-08-29 : ``payment_status`` reste ``to_be_processed`` alors que ``paid`` passe
        # à true et le reste à payer à 0). Une facture ainsi réglée EST payée : demander
        # « payée » n'exige aucun envoi ; demander « à payer » est contradictoire → ambigu.
        if raw.get("paid") is True:
            label = f"{observed} (paid=true)"
            if requested == "paid":
                return _StateVerdict("match", label)
            return _StateVerdict("ambiguous", label)
        if observed in _SUPPLIER_OPPOSITE[requested]:
            return _StateVerdict("mismatch", observed)
        return _StateVerdict("ambiguous", observed)

    async def _probe_customer_paid(self, invoice_id: int) -> _StateVerdict:
        """Relit ``paid`` de la facture client (``mark_as_paid`` = drapeau simple)."""
        try:
            raw = await self._client.request("GET", f"/customer_invoices/{invoice_id}")
        except (PennylaneError, httpx.HTTPError, ValueError):
            return _StateVerdict("unverifiable", None)
        paid = raw.get("paid")
        if paid is True:
            return _StateVerdict("match", "paid=true")
        if paid is False:
            return _StateVerdict("mismatch", "paid=false")
        return _StateVerdict("unverifiable", None)

    async def _wal_write(
        self,
        *,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        namespace: str,
        operation: str,
        state_probe: Callable[[], Awaitable[_StateVerdict]] | None = None,
    ) -> InvoiceWriteAck:
        """Écriture ``204 No Content`` adossée au WAL (anti-doublon + IN_DOUBT humain).

        ``namespace`` = clé de tenancy opaque (p. ex. ``dossier_id``) scopant le WAL.
        Le fingerprint d'idempotence est calculé sur ``(method, path, body)`` — un
        re-appel identique court-circuite (CONFIRMED → zéro POST). ``operation`` est un
        libellé humain (porté dans l'accusé), HORS empreinte (changer le libellé ne
        change pas le fingerprint).

        ``state_probe`` (PUT de statut uniquement) : sonde F-1 exécutée au court-circuit.
        Un ``mismatch`` invalide le mapping sans id (``CONFIRMED → FAILED``) hors de la
        section critique, puis rejoue UNE seule passe — même patron borné que
        ``LedgerEntriesResource.create``. Sans sonde, le court-circuit garde sa
        sémantique historique (« déjà envoyé »).
        """
        # STORE-1 : jamais de write réel sur un NullStore (no-op) ni un
        # store absent — refus fail-fast AVANT le moindre effet (sinon aucune garantie
        # anti-doublon). NullStore reste réservé à la lecture seule / aux tests.
        if self._wal_store is None or isinstance(self._wal_store, NullStore):
            raise WALStoreRequiredError()

        wal = self._get_wal()
        stale: _StaleConfirmedStateError | None = None
        for _attempt in (1, 2):
            try:
                return await self._wal_write_once(
                    method=method,
                    path=path,
                    body=body,
                    namespace=namespace,
                    operation=operation,
                    state_probe=state_probe,
                    stale_observed=stale.observed if stale is not None else None,
                )
            except _StaleConfirmedStateError as exc:
                stale = exc
                # Hors de la section critique (le verrou (ns,fp) est relâché) : invalidation
                # conditionnelle par identité « sans id ». False = mapping changé entre-temps
                # (concurrent) → la passe suivante court-circuite sur le mapping FRAIS et
                # re-sonde ; True = FAILED → la passe suivante renvoie le PUT.
                await wal.invalidate_confirmed(
                    namespace,
                    exc.fingerprint,
                    expected_pennylane_id=None,
                    reason=(
                        f"{operation} : état courant {exc.observed!r} contraire au statut "
                        "demandé — mapping sans id invalidé, renvoi autorisé (F-1)"
                    ),
                )
                continue
        assert stale is not None
        raise WALInDoubtError(
            fingerprint=stale.fingerprint,
            candidates=[],
            reason=(
                f"{operation} : état courant {stale.observed!r} toujours contraire après "
                "invalidation et rejeu — concurrence anormale, vérification humaine"
            ),
        ) from stale

    async def _wal_write_once(
        self,
        *,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        namespace: str,
        operation: str,
        state_probe: Callable[[], Awaitable[_StateVerdict]] | None,
        stale_observed: str | None = None,
    ) -> InvoiceWriteAck:
        """Une passe : réservation → (court-circuit sondé) ou envoi unique → issue.
        ``stale_observed`` (2ᵉ passe F-1) = état courant qui contredisait le précédent
        accusé ; porté dans l'accusé du renvoi pour la traçabilité."""
        dedup_payload: dict[str, Any] = {
            "method": method,
            "path": path,
            "body": body or {},
        }
        wal = self._get_wal()
        async with wal.reserve(namespace=namespace, payload=dedup_payload) as resv:
            if resv.should_short_circuit:
                if state_probe is None:
                    # Rapprochement (POST) : le « déjà fait » atteste l'envoi historique.
                    return InvoiceWriteAck(
                        operation=operation,
                        status="already_confirmed",
                        detail=(
                            f"{operation} déjà envoyé (idempotence WAL : même empreinte, "
                            "zéro POST). Atteste l'historique, pas l'état courant."
                        ),
                    )
                verdict = await state_probe()
                if verdict.verdict == "mismatch":
                    raise _StaleConfirmedStateError(resv.fingerprint, verdict.observed)
                if verdict.verdict == "match":
                    return InvoiceWriteAck(
                        operation=operation,
                        status="already_confirmed",
                        detail=(
                            f"{operation} déjà confirmé et état courant conforme "
                            f"({verdict.observed}) — zéro envoi."
                        ),
                        observed_state=verdict.observed,
                        state_verified=True,
                    )
                if verdict.verdict == "ambiguous":
                    reason = f"état courant ambigu ({verdict.observed})"
                else:
                    reason = "état courant invérifiable (relecture en échec)"
                return InvoiceWriteAck(
                    operation=operation,
                    status="already_confirmed",
                    detail=(
                        f"{operation} déjà envoyé, mais {reason} — aucun renvoi automatique, "
                        "vérifier la facture chez Pennylane."
                    ),
                    observed_state=verdict.observed,
                    state_verified=False,
                )
            if resv.is_duplicate_in_progress:
                raise WALDuplicatePayloadError(
                    fingerprint=resv.fingerprint, wal_state=resv.duplicate_state
                )
            # PROCEEDING : IN_FLIGHT durable AVANT l'envoi. UN SEUL envoi (request_write ne
            # retente que 429 + ConnectError = octets jamais partis ; tout doute → IN_DOUBT).
            try:
                await self._client.request_write(method, path, json=body)
            except (
                PennylaneClientError,
                PennylaneAuthError,
                PennylaneRateLimitError,
            ) as exc:
                # 4xx (dont 422) / 401-403 / 429 épuisé : refus déterministe, rien atterri.
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
                # État INCERTAIN (5xx / timeout après envoi / réponse illisible) : l'opération
                # a PU aboutir → IN_DOUBT (jamais de re-soumission automatique,).
                # Résolution humaine : vérifier l'état chez Pennylane puis WALManager.resolve.
                await resv.mark_in_doubt(repr(exc))
                raise WALInDoubtError(
                    fingerprint=resv.fingerprint,
                    candidates=[],
                    reason=(
                        f"{operation} incertain (timeout/5xx/réponse illisible) — l'API n'a "
                        "pas confirmé l'envoi. Vérifier l'état chez Pennylane puis résoudre "
                        "(WALManager.resolve) — jamais de re-soumission automatique."
                    ),
                ) from exc
            else:
                # 204 No Content = accusé autoritatif de l'unique envoi → CONFIRMED sans id.
                await resv.confirm(None)
                if stale_observed is not None:
                    return InvoiceWriteAck(
                        operation=operation,
                        status="confirmed",
                        detail=(
                            f"{operation} renvoyé et confirmé chez Pennylane (204 No Content) : "
                            f"l'état courant ({stale_observed}) contredisait le précédent "
                            "accusé (F-1)."
                        ),
                        observed_state=stale_observed,
                        state_verified=False,
                    )
                return InvoiceWriteAck(
                    operation=operation,
                    status="confirmed",
                    detail=f"{operation} confirmé chez Pennylane (204 No Content).",
                )

    async def match_payment(
        self,
        *,
        invoice_kind: InvoiceKind,
        invoice_id: int,
        transaction_id: int,
        namespace: str,
    ) -> InvoiceWriteAck:
        """Rapproche une transaction bancaire d'une facture (fournisseur ou client).

        ``POST /{invoice_kind}_invoices/{invoice_id}/matched_transactions`` body
        ``{transaction_id}`` → 204. Adossé au WAL : un re-appel identique (même facture
        + même transaction) court-circuite (zéro POST), garde anti-double-rapprochement.

        Args:
            invoice_kind: ``"supplier"`` ou ``"customer"``.
            invoice_id: identifiant Pennylane de la facture.
            transaction_id: identifiant Pennylane de la transaction bancaire.
            namespace: clé de tenancy opaque scopant le WAL (p. ex. ``dossier_id``).
        """
        path = f"/{invoice_kind}_invoices/{invoice_id}/matched_transactions"
        return await self._wal_write(
            method="POST",
            path=path,
            body={"transaction_id": int(transaction_id)},
            namespace=namespace,
            operation=f"match_payment[{invoice_kind}#{invoice_id}<-tx#{transaction_id}]",
        )

    async def update_payment_status(
        self,
        *,
        invoice_id: int,
        payment_status: SupplierPaymentStatus,
        namespace: str,
    ) -> InvoiceWriteAck:
        """Met à jour le statut de paiement d'une facture FOURNISSEUR.

        ``PUT /supplier_invoices/{invoice_id}/payment_status`` body ``{payment_status}``
        (enum ``"paid"`` | ``"to_be_paid"``, spec OpenAPI v2.0) → 204. Adossé au WAL.

        Le type ``SupplierPaymentStatus`` (Literal) contraint l'appelant typé ; la
        validation d'une valeur non typée (entrée dict / couche MCP) est portée par la
        frontière MCP (validation bespoke — les 8 checks de création d'écriture ne
        s'appliquent pas), symétrie avec `create_entry` (validation hors lib).

        Args:
            invoice_id: identifiant Pennylane de la facture fournisseur.
            payment_status: ``"paid"`` ou ``"to_be_paid"``.
            namespace: clé de tenancy opaque scopant le WAL (p. ex. ``dossier_id``).
        """
        return await self._wal_write(
            method="PUT",
            path=f"/supplier_invoices/{invoice_id}/payment_status",
            body={"payment_status": payment_status},
            state_probe=lambda: self._probe_supplier_status(invoice_id, payment_status),
            namespace=namespace,
            operation=f"update_payment_status[supplier#{invoice_id}={payment_status}]",
        )

    async def mark_customer_invoice_paid(
        self, *, invoice_id: int, namespace: str
    ) -> InvoiceWriteAck:
        """Marque une facture CLIENT comme payée.

        ``PUT /customer_invoices/{invoice_id}/mark_as_paid`` (SANS corps, spec OpenAPI
        v2.0) → 204. Adossé au WAL : un re-appel identique court-circuite (zéro PUT).

        Args:
            invoice_id: identifiant Pennylane de la facture client.
            namespace: clé de tenancy opaque scopant le WAL (p. ex. ``dossier_id``).
        """
        return await self._wal_write(
            method="PUT",
            path=f"/customer_invoices/{invoice_id}/mark_as_paid",
            state_probe=lambda: self._probe_customer_paid(invoice_id),
            body=None,
            namespace=namespace,
            operation=f"mark_customer_invoice_paid[customer#{invoice_id}]",
        )

    async def recover(self, namespace: str | None = None) -> Sequence[dict[str, Any]]:
        """Reprise au démarrage : chaque entrée **IN_FLIGHT** (crash en vol — l'envoi a PU
        partir) bascule **IN_DOUBT** (réconciliation humaine). JAMAIS de re-POST en reprise.

        Plus simple que ``LedgerEntriesResource.recover`` : ces opérations 204-no-id ne
        « créent » pas d'entité retrouvable par signature → pas de read-back de candidats ;
        l'humain vérifie l'état chez Pennylane (statut de facture / transaction rapprochée)
        puis tranche via ``WALManager.resolve``. Les ``IN_DOUBT`` préexistants sont laissés à
        l'humain (jamais auto-résolus).

        ``namespace`` (multi-dossiers) : si fourni, SEULES les entrées de ce namespace sont
        reprises (chaque dossier a son token/client — reprendre un autre dossier serait hors
        de portée). Corps par entrée hermétique : une erreur de store n'avorte pas les
        suivantes. Retourne un rapport par entrée traitée.
        """
        wal = self._get_wal()
        report: list[dict[str, Any]] = []
        for e in await wal.iter_nonterminal():
            if namespace is not None and e.namespace != namespace:
                continue  # hors du dossier demandé → repris par SON client (multi-dossiers)
            if e.state is not WALState.IN_FLIGHT:
                continue  # IN_DOUBT déjà flaggé humain → ne pas auto-résoudre
            if e.readback_key is not None:
                # F2 : écriture LEDGER (201+id, readback_key persistée) → reprise par
                # LedgerEntriesResource.recover (read-back par signature). Ces writes paiement
                # 204-no-id n'ont PAS de readback_key → on ne traite QUE les nôtres (le store
                # WAL est partagé entre les deux types d'opérations).
                continue
            try:
                await wal.recover_in_doubt(
                    e.namespace,
                    e.fingerprint,
                    "reprise : write 204-no-id incertain au crash — vérification humaine",
                )
                report.append({"fingerprint": e.fingerprint, "action": "in_doubt"})
            except WALStoreError as exc:
                # Hermétique : on note et on CONTINUE (l'entrée reste IN_FLIGHT → re-tentée
                # à la prochaine reprise, ré-entrant). Jamais d'exception nue hors-contrat.
                report.append(
                    {"fingerprint": e.fingerprint, "action": "store_error", "error": repr(exc)}
                )
                continue
        return report
