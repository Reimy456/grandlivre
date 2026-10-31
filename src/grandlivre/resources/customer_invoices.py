"""Resource façade `/customer_invoices`.

Le tool ne résout pas d'exercice fiscal (les factures sont datées librement) ;
il prend simplement une période optionnelle. La pagination cursor est appliquée.

Symétrie avec `supplier_invoices.py` (cf. cahier des charges §4.2 B.10).

Les filtres passent par le paramètre ``filter`` (JSON, cf. ``grandlivre.filtre``) :
``date`` (``gteq`` / ``lteq``), ``customer_id`` (``eq``), ``draft`` et ``credit_note``
(``eq`` booléen, seuls « statuts » que l'API sait filtrer). L'API n'a pas de filtre ``status`` ;
passés en clair, ``date_from=`` / ``status=`` étaient ignorés en silence (défaut corrigé le
18/10/2026). Le champ ``status`` de la réponse (``paid``, ``late``, ``draft``…) se filtre côté
appelant.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from datetime import date
from typing import Any

import httpx

from ..client import PennylaneClient
from ..exceptions import (
    PennylaneAuthError,
    PennylaneClientError,
    PennylaneError,
    PennylaneRateLimitError,
    PennylaneServerError,
)
from ..filtre import clause, clauses_periode, param_filter
from ..models.customer_invoices import (
    CreditNoteLinkAck,
    CustomerInvoice,
    CustomerInvoiceImportAck,
    CustomerInvoicesPage,
)
from ..pagination import paginate, paginate_with_cap
from ..wal import (
    IdempotencyStore,
    NullStore,
    WALDuplicatePayloadError,
    WALInDoubtError,
    WALManager,
    WALStoreRequiredError,
    compute_fingerprint,
)

DEFAULT_HARD_CAP = 500
DEFAULT_PAGE_LIMIT = 100


def _booleen(value: bool) -> str:
    """Forme de la spec pour un filtre booléen : ``"true"`` / ``"false"`` (exemple
    ``credit_note`` de ``GET /customer_invoices``)."""
    return "true" if value else "false"


class CustomerInvoicesResource:
    """Façade métier pour `/customer_invoices`."""

    def __init__(
        self, client: PennylaneClient, *, wal_store: IdempotencyStore | None = None
    ) -> None:
        self._client = client
        # ``wal_store`` : requis par ``import_invoice`` (write 201+id sous WAL, ADR) ;
        # les lectures n'en ont pas besoin (``None`` → ``NullStore`` → refus typé au write).
        self._wal_store = wal_store
        self._wal: WALManager | None = None

    def _get_wal(self) -> WALManager:
        """WALManager paresseux + mémoïsé (verrous (ns, fp) persistants entre writes)."""
        if self._wal is None:
            self._wal = WALManager(self._wal_store or NullStore())
        return self._wal

    def _build_params(
        self,
        *,
        date_from: date | str | None,
        date_to: date | str | None,
        customer_id: str | None,
        draft: bool | None,
        credit_note: bool | None,
        invoice_number: str | None = None,
    ) -> dict[str, Any]:
        """``limit`` + le paramètre ``filter`` JSON (seule forme que l'API applique)."""
        clauses = clauses_periode(date_from, date_to)
        if customer_id is not None:
            clauses.append(clause("customer_id", "eq", str(customer_id)))
        if draft is not None:
            clauses.append(clause("draft", "eq", _booleen(draft)))
        if credit_note is not None:
            clauses.append(clause("credit_note", "eq", _booleen(credit_note)))
        if invoice_number is not None:
            # `invoice_number` : `eq` / `not_eq` / `in` / `not_in` (spec) — une recherche exacte
            # d'un numéro, quelle que soit la taille du dossier.
            clauses.append(clause("invoice_number", "eq", str(invoice_number)))
        return {"limit": DEFAULT_PAGE_LIMIT, **param_filter(clauses)}

    async def iterate(
        self,
        *,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        customer_id: str | None = None,
        draft: bool | None = None,
        credit_note: bool | None = None,
    ) -> AsyncIterator[CustomerInvoice]:
        """Itère sur toutes les factures clients correspondant aux filtres."""
        params = self._build_params(
            date_from=date_from,
            date_to=date_to,
            customer_id=customer_id,
            draft=draft,
            credit_note=credit_note,
        )
        async for item in paginate(
            self._client, "/customer_invoices", params=params, data_key="items"
        ):
            yield CustomerInvoice.model_validate(item)

    async def list(
        self,
        *,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        customer_id: str | None = None,
        draft: bool | None = None,
        credit_note: bool | None = None,
    ) -> list[CustomerInvoice]:
        """Liste toutes les factures (matérialisation de iterate)."""
        return [
            inv
            async for inv in self.iterate(
                date_from=date_from,
                date_to=date_to,
                customer_id=customer_id,
                draft=draft,
                credit_note=credit_note,
            )
        ]

    async def paginate(
        self,
        *,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        customer_id: str | None = None,
        draft: bool | None = None,
        credit_note: bool | None = None,
        invoice_number: str | None = None,
        cursor: str | None = None,
        cap: int = DEFAULT_HARD_CAP,
    ) -> CustomerInvoicesPage:
        """Renvoie une page de factures avec cap dur."""
        params = self._build_params(
            date_from=date_from,
            date_to=date_to,
            customer_id=customer_id,
            draft=draft,
            credit_note=credit_note,
            invoice_number=invoice_number,
        )
        if cursor is not None:
            params["cursor"] = cursor

        raw, truncated, next_cursor = await paginate_with_cap(
            self._client,
            "/customer_invoices",
            params=params,
            data_key="items",
            cap=cap,
        )

        invoices = [CustomerInvoice.model_validate(item) for item in raw]
        return CustomerInvoicesPage(
            invoices=invoices,
            truncated=truncated,
            next_cursor=next_cursor,
            count=len(invoices),
        )

    # ------------------------------------------------------------------ write (B2.3)
    @staticmethod
    def _dedup_payload(body: Mapping[str, Any], pdf: bytes) -> dict[str, Any]:
        return {
            "method": "POST",
            "path": "/customer_invoices/import",
            "body": dict(body),
            "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        }

    @classmethod
    def import_fingerprint(cls, body: Mapping[str, Any], pdf: bytes) -> str:
        """L'empreinte WAL qu'``import_invoice`` réserve pour ``(body, pdf)`` — exposée pour
        qu'un appelant relise l'entrée mémorisée (``store.get(namespace, fingerprint)``) sans
        reconstruire le payload de déduplication."""
        return compute_fingerprint(cls._dedup_payload(body, pdf))

    async def _relire(self, invoice_id: int) -> tuple[str, dict[str, Any] | None]:
        """Relecture d'une facture mémorisée : ``("ok", dict)`` si 200, ``("absente", None)``
        si 404 propre, ``("illisible", None)`` sur toute autre erreur (jamais levée ici)."""
        try:
            facture = await self._client.request("GET", f"/customer_invoices/{invoice_id}")
        except PennylaneClientError as exc:
            return ("absente", None) if exc.status_code == 404 else ("illisible", None)
        except (PennylaneError, httpx.HTTPError, json.JSONDecodeError):
            return "illisible", None
        return "ok", facture

    async def import_invoice(
        self,
        *,
        body: Mapping[str, Any],
        pdf: bytes,
        filename: str,
        namespace: str,
    ) -> CustomerInvoiceImportAck:
        """Import d'une facture client AVEC sa pièce — deux envois, UN write de référence.

        1. ``POST /file_attachments`` (multipart) → ``file_attachment_id`` ;
        2. ``POST /customer_invoices/import`` (``body`` + ``file_attachment_id``) → 201 + id.

        Idempotence WAL (ADR) sur l'empreinte ``(POST, /customer_invoices/import,
        body sans file_attachment_id, SHA-256 du PDF)`` : un re-appel identique court-circuite
        (zéro envoi) après relecture de la facture mémorisée — 404 propre → ``WALInDoubtError``
        (la facture a disparu, résolution humaine), relecture en échec → accusé
        ``already_confirmed`` non vérifié. La pièce n'est PAS le write de référence : son échec
        (4xx, 5xx, transport) laisse l'entrée FAILED et autorise un nouvel essai — au pire une
        pièce orpheline chez l'éditeur, jamais une facture en double. Après le POST d'import :
        4xx → FAILED + exception typée ; 5xx / timeout / réponse illisible → IN_DOUBT
        (``WALInDoubtError``), jamais de re-POST. Pas de ``readback_key`` : à la reprise au boot,
        une entrée restée en vol passe en doute (résolution humaine par ``external_reference``).
        ``body`` ne porte jamais ``file_attachment_id`` (posé ici) — ``ValueError`` sinon.
        """
        if self._wal_store is None or isinstance(self._wal_store, NullStore):
            raise WALStoreRequiredError()
        if "file_attachment_id" in body:
            raise ValueError(
                "import_invoice : file_attachment_id est posé par la lib après le dépôt de la "
                "pièce, jamais par l'appelant"
            )
        corps = dict(body)
        dedup_payload = self._dedup_payload(corps, pdf)
        operation = f"import_invoice[{corps.get('external_reference') or filename}]"
        wal = self._get_wal()
        async with wal.reserve(namespace=namespace, payload=dedup_payload) as resv:
            if resv.should_short_circuit:
                memorized = resv.memorized_id
                if memorized is None:
                    raise WALInDoubtError(
                        fingerprint=resv.fingerprint,
                        candidates=[],
                        reason=f"{operation} : CONFIRMED sans pennylane_id mémorisé (état incohérent)",
                    )
                etat, facture = await self._relire(int(memorized))
                if etat == "absente":
                    raise WALInDoubtError(
                        fingerprint=resv.fingerprint,
                        candidates=[int(memorized)],
                        reason=(
                            f"{operation} : facture {memorized} mémorisée INTROUVABLE chez "
                            "Pennylane (404) — vérifier par external_reference puis resolve()"
                        ),
                    )
                verifie = etat == "ok"
                return CustomerInvoiceImportAck(
                    id=int(memorized),
                    status="already_confirmed",
                    detail=(
                        f"{operation} déjà importé (idempotence WAL : même corps et même PDF, "
                        f"zéro envoi) — facture {memorized} "
                        + (
                            "relue chez Pennylane."
                            if verifie
                            else "non relue (relecture en échec), vérifier chez Pennylane."
                        )
                    ),
                    invoice_number=(
                        str(facture.get("invoice_number"))
                        if verifie and facture and facture.get("invoice_number")
                        else None
                    ),
                )
            if resv.is_duplicate_in_progress:
                raise WALDuplicatePayloadError(
                    fingerprint=resv.fingerprint, wal_state=resv.duplicate_state
                )
            # 1. La pièce (hors write de référence : tout échec = FAILED, nouvel essai permis).
            try:
                piece = await self._client.request_write(
                    "POST",
                    "/file_attachments",
                    files={"file": (filename, pdf, "application/pdf")},
                )
                attachment_id = int(piece["id"])
            except (PennylaneError, httpx.HTTPError) as exc:
                await resv.fail(f"dépôt de la pièce : {exc!r}")
                raise
            except (KeyError, TypeError, ValueError) as exc:
                await resv.fail(f"dépôt de la pièce : réponse sans id lisible ({exc!r})")
                raise PennylaneError(
                    "POST /file_attachments : réponse sans id lisible — aucun import émis"
                ) from exc
            # 2. L'import (write de référence) — UN SEUL POST, jamais de re-POST aveugle.
            try:
                raw = await self._client.request_write(
                    "POST",
                    "/customer_invoices/import",
                    json={**corps, "file_attachment_id": attachment_id},
                )
            except (
                PennylaneClientError,
                PennylaneAuthError,
                PennylaneRateLimitError,
            ) as exc:
                await resv.fail(str(exc))
                raise
            except httpx.ConnectError as exc:
                await resv.fail(f"connexion non établie : {exc!r}")
                raise
            except (
                PennylaneServerError,
                httpx.TransportError,
                httpx.DecodingError,
                json.JSONDecodeError,
            ) as exc:
                await resv.mark_in_doubt(repr(exc))
                raise WALInDoubtError(
                    fingerprint=resv.fingerprint,
                    candidates=[],
                    reason=(
                        f"{operation} : la réponse de Pennylane n'a pas confirmé l'import "
                        f"({type(exc).__name__}) — vérifier par external_reference, "
                        "jamais de re-soumission automatique"
                    ),
                ) from exc
            try:
                invoice_id = int(raw["id"])
            except (KeyError, TypeError, ValueError) as exc:
                await resv.mark_in_doubt(f"réponse 201 sans id lisible : {exc!r}")
                raise WALInDoubtError(
                    fingerprint=resv.fingerprint,
                    candidates=[],
                    reason=f"{operation} : réponse 201 sans id lisible — vérifier par external_reference",
                ) from exc
            await resv.confirm(invoice_id)
            numero = raw.get("invoice_number")
            return CustomerInvoiceImportAck(
                id=invoice_id,
                status="confirmed",
                detail=(
                    f"{operation} : facture {invoice_id} créée chez Pennylane avec sa pièce "
                    f"(file_attachment {attachment_id})."
                ),
                invoice_number=str(numero) if numero else None,
                file_attachment_id=attachment_id,
            )

    # ------------------------------------------------------------- write (B2.6, avoir)
    @staticmethod
    def _dedup_payload_e_invoice(
        invoice_options: Mapping[str, Any] | None, pdf: bytes
    ) -> dict[str, Any]:
        return {
            "method": "POST",
            "path": "/customer_invoices/e_invoices/imports",
            "invoice_options": dict(invoice_options) if invoice_options else None,
            "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        }

    @classmethod
    def e_invoice_import_fingerprint(
        cls, invoice_options: Mapping[str, Any] | None, pdf: bytes
    ) -> str:
        """L'empreinte WAL qu'``import_e_invoice`` réserve pour ``(invoice_options, pdf)``."""
        return compute_fingerprint(cls._dedup_payload_e_invoice(invoice_options, pdf))

    async def import_e_invoice(
        self,
        *,
        pdf: bytes,
        filename: str,
        namespace: str,
        invoice_options: Mapping[str, Any] | None = None,
        operation_label: str | None = None,
    ) -> CustomerInvoiceImportAck:
        """Import d'une facture électronique (PDF Factur-X) — ``POST
        /customer_invoices/e_invoices/imports`` (multipart : ``file`` + ``invoice_options`` JSON) ;
        l'éditeur lit lui-même le XML embarqué : type (380 facture, 381 avoir — montants rendus
        négatifs chez lui), numéro, dates, montants, client reconnu par son identifiant.
        ``invoice_options`` (``{customer_id?, invoice_lines?: [{e_invoice_line_id (BT-126),
        ledger_account_id?, product_id?}]}``) complète ce que le XML ne porte pas : sans compte de
        produit, le document reste ``incomplete`` (sonde B2.6 du 20/10/2026, bac à sable).

        UN seul envoi, write de référence, sous WAL : empreinte ``(POST, chemin, invoice_options,
        SHA-256 du PDF)``. Réponse 201 ``{id, url}`` — le traitement du fichier est DIFFÉRÉ (le
        document relu tout de suite est vide) : l'appelant relit avec une attente bornée. Même
        taxonomie qu'``import_invoice`` : court-circuit relu (404 → doute), 4xx → FAILED + exception
        typée, 5xx / timeout / réponse illisible → ``WALInDoubtError``, jamais de re-POST. L'import
        ne prend pas d'``external_reference`` de l'appelant (l'éditeur en génère une) : le filet
        au-delà de la WAL est la recherche exacte par numéro de facture, chez l'appelant."""
        if self._wal_store is None or isinstance(self._wal_store, NullStore):
            raise WALStoreRequiredError()
        options = dict(invoice_options) if invoice_options else None
        dedup_payload = self._dedup_payload_e_invoice(options, pdf)
        operation = f"import_e_invoice[{operation_label or filename}]"
        wal = self._get_wal()
        async with wal.reserve(namespace=namespace, payload=dedup_payload) as resv:
            if resv.should_short_circuit:
                memorized = resv.memorized_id
                if memorized is None:
                    raise WALInDoubtError(
                        fingerprint=resv.fingerprint,
                        candidates=[],
                        reason=f"{operation} : CONFIRMED sans pennylane_id mémorisé (état incohérent)",
                    )
                etat, facture = await self._relire(int(memorized))
                if etat == "absente":
                    raise WALInDoubtError(
                        fingerprint=resv.fingerprint,
                        candidates=[int(memorized)],
                        reason=(
                            f"{operation} : document {memorized} mémorisé INTROUVABLE chez "
                            "Pennylane (404) — vérifier par numéro de facture puis resolve()"
                        ),
                    )
                verifie = etat == "ok"
                return CustomerInvoiceImportAck(
                    id=int(memorized),
                    status="already_confirmed",
                    detail=(
                        f"{operation} déjà importé (idempotence WAL : même PDF, mêmes options, "
                        f"zéro envoi) — document {memorized} "
                        + (
                            "relu chez Pennylane."
                            if verifie
                            else "non relu, vérifier chez Pennylane."
                        )
                    ),
                    invoice_number=(
                        str(facture.get("invoice_number"))
                        if verifie and facture and facture.get("invoice_number")
                        else None
                    ),
                )
            if resv.is_duplicate_in_progress:
                raise WALDuplicatePayloadError(
                    fingerprint=resv.fingerprint, wal_state=resv.duplicate_state
                )
            parties: dict[str, tuple[str | None, bytes | str, str]] = {
                "file": (filename, pdf, "application/pdf")
            }
            if options:
                parties["invoice_options"] = (
                    None,
                    json.dumps(options, ensure_ascii=False, separators=(",", ":")),
                    "application/json",
                )
            try:
                raw = await self._client.request_write(
                    "POST", "/customer_invoices/e_invoices/imports", files=parties
                )
            except (
                PennylaneClientError,
                PennylaneAuthError,
                PennylaneRateLimitError,
            ) as exc:
                await resv.fail(str(exc))
                raise
            except httpx.ConnectError as exc:
                await resv.fail(f"connexion non établie : {exc!r}")
                raise
            except (
                PennylaneServerError,
                httpx.TransportError,
                httpx.DecodingError,
                json.JSONDecodeError,
            ) as exc:
                await resv.mark_in_doubt(repr(exc))
                raise WALInDoubtError(
                    fingerprint=resv.fingerprint,
                    candidates=[],
                    reason=(
                        f"{operation} : la réponse de Pennylane n'a pas confirmé l'import "
                        f"({type(exc).__name__}) — vérifier par numéro de facture, jamais de "
                        "re-soumission automatique"
                    ),
                ) from exc
            try:
                document_id = int(raw["id"])
            except (KeyError, TypeError, ValueError) as exc:
                await resv.mark_in_doubt(f"réponse 201 sans id lisible : {exc!r}")
                raise WALInDoubtError(
                    fingerprint=resv.fingerprint,
                    candidates=[],
                    reason=f"{operation} : réponse 201 sans id lisible — vérifier par numéro de facture",
                ) from exc
            await resv.confirm(document_id)
            return CustomerInvoiceImportAck(
                id=document_id,
                status="confirmed",
                detail=(
                    f"{operation} : document {document_id} reçu par Pennylane (traitement du "
                    "fichier différé : relire avant de s'y fier)."
                ),
            )

    async def link_credit_note(
        self, *, invoice_id: int, credit_note_id: int, namespace: str
    ) -> CreditNoteLinkAck:
        """Rattache un avoir à sa facture d'origine — ``POST /customer_invoices/{invoice_id}/
        link_credit_note`` ``{credit_note_id}`` (200 : la facture rendue). Chez l'éditeur, l'avoir
        passe ``cancelled`` avec ``credited_invoice`` et la facture ``partially_cancelled`` (ou
        ``cancelled``), son reste à payer diminué du montant de l'avoir (sonde B2.6 du 20/10/2026).

        Write de référence sous WAL : empreinte ``(POST, chemin, corps)`` ; court-circuit sans
        relecture (l'appelant relit l'avoir et la facture) ; 4xx → FAILED + exception typée ;
        5xx / timeout / réponse illisible → ``WALInDoubtError``, jamais de re-POST."""
        if self._wal_store is None or isinstance(self._wal_store, NullStore):
            raise WALStoreRequiredError()
        chemin = f"/customer_invoices/{int(invoice_id)}/link_credit_note"
        corps = {"credit_note_id": int(credit_note_id)}
        operation = f"link_credit_note[{invoice_id} ← {credit_note_id}]"
        wal = self._get_wal()
        async with wal.reserve(
            namespace=namespace, payload={"method": "POST", "path": chemin, "body": corps}
        ) as resv:
            if resv.should_short_circuit:
                return CreditNoteLinkAck(
                    invoice_id=int(invoice_id),
                    credit_note_id=int(credit_note_id),
                    status="already_confirmed",
                    detail=f"{operation} déjà rattaché (idempotence WAL, zéro envoi).",
                )
            if resv.is_duplicate_in_progress:
                raise WALDuplicatePayloadError(
                    fingerprint=resv.fingerprint, wal_state=resv.duplicate_state
                )
            try:
                raw = await self._client.request_write("POST", chemin, json=corps)
            except (
                PennylaneClientError,
                PennylaneAuthError,
                PennylaneRateLimitError,
            ) as exc:
                await resv.fail(str(exc))
                raise
            except httpx.ConnectError as exc:
                await resv.fail(f"connexion non établie : {exc!r}")
                raise
            except (
                PennylaneServerError,
                httpx.TransportError,
                httpx.DecodingError,
                json.JSONDecodeError,
            ) as exc:
                await resv.mark_in_doubt(repr(exc))
                raise WALInDoubtError(
                    fingerprint=resv.fingerprint,
                    candidates=[int(credit_note_id)],
                    reason=(
                        f"{operation} : la réponse de Pennylane n'a pas confirmé le rattachement "
                        f"({type(exc).__name__}) — relire l'avoir (credited_invoice), jamais de "
                        "re-soumission automatique"
                    ),
                ) from exc
            await resv.confirm(int(invoice_id))
            statut = raw.get("status") if isinstance(raw, dict) else None
            reste = raw.get("remaining_amount_with_tax") if isinstance(raw, dict) else None
            return CreditNoteLinkAck(
                invoice_id=int(invoice_id),
                credit_note_id=int(credit_note_id),
                status="confirmed",
                detail=f"{operation} : avoir rattaché à la facture chez Pennylane.",
                invoice_status=str(statut) if statut is not None else None,
                remaining_amount_with_tax=str(reste) if reste is not None else None,
            )
