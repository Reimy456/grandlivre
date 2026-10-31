"""WAL outbox transactionnel — anti-doublon write (ADR).

Machine d'états ``PENDING → IN_FLIGHT → CONFIRMED | FAILED | IN_DOUBT``. Clé
d'idempotence (UUIDv4) + fingerprint SHA-256 du payload canonicalisé persistés
**avant** le POST.

Modèle de concurrence (§2.14) :

- **Intra-process** : ``anyio.Lock`` keyé ``(namespace, fingerprint)`` sérialise
  toute la fenêtre check → IN_FLIGHT → POST → transition (le verrou est tenu par
  le bloc ``async with WALManager.reserve(...)`` de l'appelant, POST compris).
- **Cross-process** : le marqueur ``IN_FLIGHT`` **durable**, écrit sous ``flock``
  + ``fsync`` **avant** que le POST parte, est le garde. Un 2ᵉ process qui le lit
  sous ``flock`` voit l'état non terminal et lève ``WALDuplicatePayloadError`` —
  il ne POSTe pas. ``flock`` n'est tenu que sur les opérations store brèves
  (jamais across le POST réseau).

CRUX (§2.14, invariant #1) : ``IN_FLIGHT`` est ``fsync`` **avant** le POST, jamais
après. Au recovery : ``PENDING`` = POST pas parti = (re)tentable ; ``IN_FLIGHT`` =
le POST a pu partir = **réconciliation, jamais blind-retry** (cf.
``classify_for_recovery``). Le read-back de réconciliation effectif vit avec
``LedgerEntriesResource.create()`` (ADR §2.4, livré Cs 4).

Surface publique (§2.11) : seul ``WALDuplicatePayloadError`` est exporté. Les
appelants doivent l'attraper. ``WALManager`` / ``WALEntry`` / ``WALState`` / les
stores restent internes — passer JSONL → SQLite ne casse pas l'API.

Tenancy hors lib (§2.11) : ``namespace: str`` opaque (p. ex. ``dossier_id``),
fourni par la couche appelante. La lib ne connaît aucun type applicatif.
"""

from __future__ import annotations

import fcntl
import functools
import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
import warnings
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import anyio

from .exceptions import PennylaneError

_HINT_WAL_DUPLICATE = (
    "Payload déjà en cours (IN_FLIGHT) ou en doute (IN_DOUBT) dans le WAL : ne pas "
    "re-soumettre. Si IN_DOUBT, l'écriture doit être réconciliée (read-back) puis "
    "tranchée humainement avant tout nouvel essai."
)
_HINT_WAL_IN_DOUBT = (
    "Écriture en DOUTE technique : le POST a échoué (5xx/timeout) et le read-back n'a "
    "pas pu trancher. L'écriture A PEUT-ÊTRE atterri — JAMAIS de résolution auto. "
    "Vérifier humainement chez Pennylane si l'écriture existe, puis appeler "
    "WALManager.resolve(landed=True|False) (CONFIRMED si présente, FAILED sinon)."
)


def _utcnow_iso() -> str:
    """Horodatage UTC ISO 8601 (helper isolé pour patch en test)."""
    return datetime.now(tz=UTC).isoformat()


def _new_idempotency_key() -> str:
    """Clé d'idempotence UUIDv4 (helper isolé pour patch en test)."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# États + classification recovery
# ---------------------------------------------------------------------------


class WALState(StrEnum):
    """États de la machine outbox (ADR)."""

    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    IN_DOUBT = "IN_DOUBT"


class RecoveryAction(StrEnum):
    """Décision de reprise pour une entrée rechargée (process restart)."""

    RETRY_OK = "RETRY_OK"  # PENDING : POST pas parti, (re)tentable sans risque
    RECONCILE = "RECONCILE"  # IN_FLIGHT / IN_DOUBT : read-back, jamais blind-retry
    DONE = "DONE"  # CONFIRMED : id mémorisé, rien à faire
    FAILED_TERMINAL = "FAILED_TERMINAL"  # FAILED : refus déterministe, terminal


def classify_for_recovery(state: WALState) -> RecoveryAction:
    """Mappe un état rechargé vers l'action de reprise (CRUX §2.14, invariant #1).

    ``IN_FLIGHT`` → ``RECONCILE`` (jamais ``RETRY_OK``) : le POST a pu partir, un
    blind-retry créerait un doublon.
    """
    if state is WALState.PENDING:
        return RecoveryAction.RETRY_OK
    if state in (WALState.IN_FLIGHT, WALState.IN_DOUBT):
        return RecoveryAction.RECONCILE
    if state is WALState.CONFIRMED:
        return RecoveryAction.DONE
    return RecoveryAction.FAILED_TERMINAL


# ---------------------------------------------------------------------------
# Fingerprint déterministe (multiset de lignes, §2.14)
# ---------------------------------------------------------------------------


def _line_sort_key(line: Mapping[str, Any]) -> tuple[int, str, str, str]:
    return (
        # Pas de défaut 0 silencieux (cohérence RB-3) : le payload (NewLedgerEntry dump)
        # a TOUJOURS ledger_account_id (champ requis) ; absent = bug → KeyError loud.
        int(line["ledger_account_id"]),
        str(line.get("debit", "")),
        str(line.get("credit", "")),
        str(line.get("label", "")),
    )


def compute_fingerprint(payload: Mapping[str, Any]) -> str:
    """SHA-256 du payload canonicalisé — déterministe, robuste au réordonnancement.

    Les ``ledger_entry_lines`` sont **triées** (multiset : l'ordre n'est pas
    sémantique en double-partie, §2.14) avant le hash ; les clés des dicts sont
    triées (``sort_keys``). Le payload POST conserve l'ordre appelant ; seul le
    hash trie (séparation dedup / envoi).
    """
    canonical: dict[str, Any] = dict(payload)
    lines = canonical.get("ledger_entry_lines")
    if isinstance(lines, list):
        canonical["ledger_entry_lines"] = sorted(lines, key=_line_sort_key)
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Erreur publique
# ---------------------------------------------------------------------------


class WALDuplicatePayloadError(PennylaneError):
    """Le payload est déjà en cours (IN_FLIGHT) ou en doute (IN_DOUBT) dans le WAL.

    Erreur métier (pas HTTP) : ``status_code=None``. Levée AVANT tout POST par
    ``LedgerEntriesResource.create()`` quand le WAL signale un doublon non
    terminal. L'attribut ``wal_state`` porte l'état (``IN_FLIGHT`` / ``IN_DOUBT``)
    et ``fingerprint`` l'empreinte du payload. Surface publique (§2.11) : les
    appelants DOIVENT l'attraper.
    """

    def __init__(self, *, fingerprint: str, wal_state: WALState) -> None:
        super().__init__(
            f"Payload déjà présent dans le WAL en état {wal_state.value} "
            f"(fingerprint={fingerprint[:12]}…) — ré-émission refusée.",
            status_code=None,
            hint=_HINT_WAL_DUPLICATE,
        )
        self.fingerprint = fingerprint
        self.wal_state = wal_state


class WALInDoubtError(PennylaneError):
    """Écriture en DOUTE TECHNIQUE — « a-t-elle atterri ? » (ADR).

    Levée par `create()` quand le POST a échoué (5xx/timeout) et que le read-back de
    réconciliation n'a pas pu trancher automatiquement : soit `N>1` candidats
    ambigus, soit `0` candidat mais après épuisement des tentatives (et un POST a pu
    arriver tardivement). L'écriture A PEUT-ÊTRE atterri → **JAMAIS de résolution
    auto** (une écriture manquante silencieuse, ou un doublon, sont tous deux pires).

    C'est un doute **technique** (pas un doute de contenu — celui-ci relève de la
    boucle métier applicative). Résolution **humaine** : vérifier chez Pennylane si
    l'écriture existe, puis `WALManager.resolve(namespace, fingerprint, landed=...)`.
    L'attribut `candidates` porte les `id` Pennylane trouvés au read-back (à présenter
    à l'humain) ; le FLUX humain (présenter, capturer oui/non) est applicatif (hors
    lib, zéro-couplage). Surface publique (§2.11) : les appelants DOIVENT l'attraper.
    """

    def __init__(self, *, fingerprint: str, candidates: list[int], reason: str) -> None:
        super().__init__(
            f"Écriture en doute technique, réconciliation humaine requise : {reason} "
            f"(fingerprint={fingerprint[:12]}…, {len(candidates)} candidat(s)).",
            status_code=None,
            hint=_HINT_WAL_IN_DOUBT,
        )
        self.fingerprint = fingerprint
        self.candidates = candidates
        self.reason = reason


class WALStoreError(PennylaneError):
    """Défaillance du STORE d'idempotence (ADR, revue v4 — CI-2 / NB3-3).

    Enveloppe toute erreur au niveau du store durable : driver indisponible
    (``sqlite3.Error`` / ``psycopg.Error``) OU violation de la garde
    ``UNIQUE(namespace, pennylane_id) WHERE state='CONFIRMED'`` (un même ``pennylane_id``
    confirmé sous un AUTRE fingerprint). ``status_code=None`` (erreur interne, pas HTTP).
    Surface publique (§2.11) : ``create()`` / ``recover()`` l'attrapent → ``IN_DOUBT`` —
    jamais d'exception nue hors-contrat ni de confirmation silencieuse sur le mauvais id.
    """

    def __init__(self, message: str, *, conflict: bool = False) -> None:
        super().__init__(
            message,
            status_code=None,
            hint=(
                "Défaillance du store WAL : écriture en doute technique → réconciliation "
                "humaine (jamais de confirm/dédup silencieux)."
            ),
        )
        self.conflict = conflict


@contextmanager
def _jsonl_errors() -> Iterator[None]:
    """CI-2 / HERM-2 : convertit toute erreur d'E/S du ``JsonlStore`` (``OSError`` disque
    plein / fichier perdu, ``ValueError`` JSON corrompu) en ``WALStoreError`` — parité avec
    SQLite/PG. Sans cela un ``OSError`` nu échapperait de ``confirm()`` ET masquerait
    l'exception POST d'origine dans le finally de ``reserve()`` (la double-faute que CI-2
    prétend prévenir). ``conflict=False`` : une E/S ratée n'est pas un conflit d'unicité."""
    try:
        yield
    except (OSError, ValueError) as exc:
        raise WALStoreError(f"JSONL store error: {exc}", conflict=False) from exc


def _parse_readback_key(rb: str | None) -> dict[str, Any] | None:
    """Désérialise la colonne ``readback_key`` du store SQLite (NBI-2-RESIDUAL-1/2) — UNIQUE
    point de ``json.loads`` de ce store, utilisé par ``_row_to_entry`` (chemin READ) ET par le
    re-commit de ``reserve_in_flight`` (chemin WRITE, ligne PENDING/FAILED rejouée). Une valeur
    JSON malformée (torn-write d'une colonne / drift schéma v3→v4) sur base SAINE lèverait
    ``json.JSONDecodeError`` NU (⊂ ``ValueError``, ∉ ``sqlite3.Error`` → hors des gardes driver)
    → enveloppe ``WALStoreError(conflict=False)`` : AUCUN chemin ne fait fuir d'erreur de
    désérialisation hors-contrat §2.11, aucun site sœur futur possible (un seul point).
    (Postgres immune : JSONB→dict natif ; JSONL immune : ``_load_all`` saute par-ligne.)"""
    if not rb:
        return None
    try:
        parsed = json.loads(rb)
    except (json.JSONDecodeError, ValueError) as exc:
        raise WALStoreError(
            f"SQLite store error (readback_key illisible): {exc}", conflict=False
        ) from exc
    if not isinstance(parsed, dict):
        # Belt-and-suspenders (reco C re-revue ultra-tight) : JSON VALIDE mais d'un autre type
        # (liste / scalaire — proba ~nulle, la colonne est toujours json.dumps(dict)). Sinon
        # échouerait plus tard en TypeError au matching → fermeture totale ici (exception ∪ type).
        raise WALStoreError(
            "SQLite store error (readback_key n'est pas un objet JSON)", conflict=False
        )
    parsed_dict: dict[str, Any] = parsed
    return parsed_dict


# ---------------------------------------------------------------------------
# Entrée WAL + résultat de réservation
# ---------------------------------------------------------------------------


@dataclass
class WALEntry:
    """Une ligne de l'outbox (interne). Sérialisable JSONL via ``asdict``."""

    namespace: str
    fingerprint: str
    idempotency_key: str
    state: WALState
    pennylane_id: int | None = None
    attempts: int = 0
    last_error: str | None = None
    created_at: str = ""
    updated_at: str = ""
    # Clé de read-back persistée AVANT le POST (ADR) : recover() ne peut PAS la
    # reconstruire depuis le fingerprint (hash irréversible). Forme : {date, journal_id,
    # line_signature: [[account_id, debit_str, credit_str], ...], piece_number}.
    readback_key: dict[str, Any] | None = None
    # id_max capturé pré-POST (GET sort=-id) : recover() identifie NOTRE écriture par
    # id > id_watermark (même méthode robuste que le read-back en-session, anti-skew).
    id_watermark: int | None = None

    def to_json_line(self) -> str:
        data = asdict(self)
        data["state"] = self.state.value
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json_obj(cls, obj: Mapping[str, Any]) -> WALEntry:
        return cls(
            namespace=str(obj["namespace"]),
            fingerprint=str(obj["fingerprint"]),
            idempotency_key=str(obj["idempotency_key"]),
            state=WALState(obj["state"]),
            pennylane_id=obj.get("pennylane_id"),
            attempts=int(obj.get("attempts", 0)),
            last_error=obj.get("last_error"),
            created_at=str(obj.get("created_at", "")),
            updated_at=str(obj.get("updated_at", "")),
            readback_key=obj.get("readback_key"),
            id_watermark=obj.get("id_watermark"),
        )


class ReserveOutcome(StrEnum):
    """Issue d'une réservation (``IdempotencyStore.reserve_in_flight``)."""

    PROCEEDING = "PROCEEDING"  # IN_FLIGHT écrit (durable) ; l'appelant POSTe
    SHORT_CIRCUIT = "SHORT_CIRCUIT"  # déjà CONFIRMED ; id mémorisé, zéro POST
    DUPLICATE = "DUPLICATE"  # IN_FLIGHT/IN_DOUBT en cours ; ne pas POSTer


@dataclass
class ReserveResult:
    """Résultat de ``reserve_in_flight`` (atomique sous flock)."""

    outcome: ReserveOutcome
    entry: WALEntry
    # pennylane_id renseigné uniquement sur SHORT_CIRCUIT.
    memorized_id: int | None = None


# ---------------------------------------------------------------------------
# Store — Protocol + JSONL (phase 1) + Null
# ---------------------------------------------------------------------------


class IdempotencyStore(Protocol):
    """Store durable d'idempotence (ADR). Implémentations injectables.

    Les méthodes sont **synchrones** (IO fichier/DB) ; ``WALManager`` les exécute via
    ``anyio.to_thread`` pour ne pas bloquer l'event loop.

    ``cross_host_safe`` (ADR) : ``True`` SEULEMENT si le store sérialise les
    écritures concurrentes ENTRE MACHINES (p. ex. Postgres ``UNIQUE(namespace,
    fingerprint)``). Les stores mono-host (SQLite, JSONL) valent ``False`` — le garde de
    démarrage (``ensure_deployment_safety``) refuse alors un déploiement multi-writer.
    """

    cross_host_safe: bool

    def reserve_in_flight(
        self,
        namespace: str,
        fingerprint: str,
        idempotency_key: str,
        *,
        readback_key: dict[str, Any] | None = None,
        id_watermark: int | None = None,
    ) -> ReserveResult:
        """Check-and-set atomique (sous verrou cross-process) : écrit ``IN_FLIGHT``
        si l'écriture peut partir, sinon SHORT_CIRCUIT (CONFIRMED) ou DUPLICATE.

        ``readback_key`` + ``id_watermark`` sont persistés AVANT le POST (CRUX §2.14) pour
        que ``recover()`` puisse réconcilier après un crash (le fingerprint seul ne suffit
        pas — hash irréversible)."""
        ...

    def finalize(
        self,
        namespace: str,
        fingerprint: str,
        state: WALState,
        pennylane_id: int | None,
        last_error: str | None,
    ) -> WALEntry:
        """Transition terminale/IN_DOUBT (sous verrou), après le POST."""
        ...

    def get(self, namespace: str, fingerprint: str) -> WALEntry | None:
        """Lecture d'une entrée (recovery / tests)."""
        ...

    def iter_nonterminal(self) -> list[WALEntry]:
        """Toutes les entrées NON terminales (``IN_FLIGHT`` / ``IN_DOUBT``) — pour
        ``recover()`` au démarrage. Les états terminaux (CONFIRMED/FAILED) sont exclus."""
        ...

    def find_confirmed_fingerprint(self, namespace: str, pennylane_id: int) -> str | None:
        """Fingerprint d'une entrée ``CONFIRMED`` portant ce ``pennylane_id`` dans ce
        ``namespace`` (sinon ``None``). Garde « écriture sœur » (ADR) : refuser de
        confirmer un id déjà CONFIRMED sous un AUTRE fingerprint → IN_DOUBT."""
        ...

    def transition_confirmed_if(
        self,
        namespace: str,
        fingerprint: str,
        expected_pennylane_id: int | None,
        to_state: WALState,
        reason: str,
    ) -> bool:
        """Transition CONDITIONNELLE par IDENTITÉ (revue (c) D2, généralisée RES-1/RES-2) :
        ``CONFIRMED → to_state`` (``FAILED`` = ré-écriture autorisée ; ``IN_DOUBT`` =
        réconciliation humaine via ``resolve()`` ; ``pennylane_id`` remis à NULL) SI ET
        SEULEMENT SI l'entrée est ``CONFIRMED`` ET porte ``expected_pennylane_id``.
        ATOMIQUE au niveau store (équivalent ``UPDATE … WHERE state='CONFIRMED' AND
        pennylane_id=?`` + rows-affected) — sous concurrence cross-process, un appelant
        tenant un id PÉRIMÉ ne peut jamais écraser un mapping re-confirmé sur un autre id
        vivant. ``True`` = transition faite, ``False`` = mapping changé entre-temps
        (aucune écriture)."""
        ...


class JsonlStore:
    """Store JSONL durable (ADR) — store mono-host hérité.

    Conservé pour rétro-compatibilité ; ``SqliteIdempotencyStore`` est le store durable
    par défaut recommandé (atomicité ``UNIQUE(namespace, fingerprint)`` native).

    Concurrence cross-process (mono-host) : ``flock`` exclusif sur un sibling
    ``<path>.lock`` **stable** tenu sur tout le read-modify-write. Durabilité : réécriture
    via tempfile + ``fsync(fd)`` + ``fsync(dir)`` + ``rename`` atomique POSIX (pattern
    ``memoire_tampon/state.py`` réutilisé, pas importé — zéro-couplage). Reprise tolérante
    au torn-write : une ligne JSON malformée est ignorée au chargement.
    """

    # Mono-host (flock) : ne sérialise PAS entre machines (ADR).
    cross_host_safe: bool = False

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock_path = self._path.with_name(self._path.name + ".lock")
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # -- IO bas niveau (sous flock du caller) --

    def _load_all(self) -> dict[tuple[str, str], WALEntry]:
        entries: dict[tuple[str, str], WALEntry] = {}
        if not self._path.exists():
            return entries
        with self._path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    entry = WALEntry.from_json_obj(obj)
                except (json.JSONDecodeError, KeyError, ValueError):
                    # Torn-write / ligne corrompue : on saute (reprise défensive).
                    continue
                entries[(entry.namespace, entry.fingerprint)] = entry
        return entries

    def _persist_all(self, entries: dict[tuple[str, str], WALEntry]) -> None:
        parent = self._path.parent
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                for entry in entries.values():
                    tmp.write(entry.to_json_line() + "\n")
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = tmp.name
            os.rename(tmp_path, self._path)
            tmp_path = None
            # fsync du répertoire parent : durabilité du rename lui-même (§2.7).
            dir_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if tmp_path is not None and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _with_lock(self) -> _FlockGuard:
        return _FlockGuard(self._lock_path)

    # -- API store --

    def reserve_in_flight(
        self,
        namespace: str,
        fingerprint: str,
        idempotency_key: str,
        *,
        readback_key: dict[str, Any] | None = None,
        id_watermark: int | None = None,
    ) -> ReserveResult:
        with self._with_lock(), _jsonl_errors():
            entries = self._load_all()
            existing = entries.get((namespace, fingerprint))
            if existing is not None and existing.state is WALState.CONFIRMED:
                return ReserveResult(
                    outcome=ReserveOutcome.SHORT_CIRCUIT,
                    entry=existing,
                    memorized_id=existing.pennylane_id,
                )
            if existing is not None and existing.state in (
                WALState.IN_FLIGHT,
                WALState.IN_DOUBT,
            ):
                return ReserveResult(outcome=ReserveOutcome.DUPLICATE, entry=existing)
            # absent / PENDING / FAILED → on (re)commit IN_FLIGHT (rien n'a atterri).
            now = _utcnow_iso()
            attempts = (existing.attempts if existing is not None else 0) + 1
            created_at = existing.created_at if existing is not None else now
            key = existing.idempotency_key if existing is not None else idempotency_key
            # Réserve : conserve la clé de read-back / le watermark déjà persistés si une
            # réservation antérieure les portait (re-commit après FAILED), sinon prend les
            # nouveaux fournis par l'appelant.
            rb_key = (
                readback_key
                if readback_key is not None
                else (existing.readback_key if existing is not None else None)
            )
            watermark = (
                id_watermark
                if id_watermark is not None
                else (existing.id_watermark if existing is not None else None)
            )
            entry = WALEntry(
                namespace=namespace,
                fingerprint=fingerprint,
                idempotency_key=key,
                state=WALState.IN_FLIGHT,
                attempts=attempts,
                created_at=created_at,
                updated_at=now,
                readback_key=rb_key,
                id_watermark=watermark,
            )
            entries[(namespace, fingerprint)] = entry
            self._persist_all(entries)  # IN_FLIGHT + clé read-back durables AVANT le POST (CRUX)
            return ReserveResult(outcome=ReserveOutcome.PROCEEDING, entry=entry)

    def finalize(
        self,
        namespace: str,
        fingerprint: str,
        state: WALState,
        pennylane_id: int | None,
        last_error: str | None,
    ) -> WALEntry:
        with self._with_lock(), _jsonl_errors():
            entries = self._load_all()
            # Garde écriture-sœur (NB3-3, émule UNIQUE(ns, pennylane_id) WHERE CONFIRMED) :
            # un même pennylane_id confirmé sous un AUTRE fingerprint → conflit → IN_DOUBT.
            if state is WALState.CONFIRMED and pennylane_id is not None:
                for (ns_e, fp_e), other in entries.items():
                    if (
                        ns_e == namespace
                        and fp_e != fingerprint
                        and other.state is WALState.CONFIRMED
                        and other.pennylane_id == pennylane_id
                    ):
                        raise WALStoreError(
                            f"pennylane_id {pennylane_id} déjà CONFIRMED sous un autre "
                            f"fingerprint (namespace {namespace})",
                            conflict=True,
                        )
            existing = entries.get((namespace, fingerprint))
            now = _utcnow_iso()
            if existing is None:
                # Défensif : finalize sans réservation préalable.
                existing = WALEntry(
                    namespace=namespace,
                    fingerprint=fingerprint,
                    idempotency_key=_new_idempotency_key(),
                    state=state,
                    created_at=now,
                )
            existing.state = state
            existing.pennylane_id = pennylane_id
            existing.last_error = last_error
            existing.updated_at = now
            entries[(namespace, fingerprint)] = existing
            self._persist_all(entries)
            return existing

    def get(self, namespace: str, fingerprint: str) -> WALEntry | None:
        with self._with_lock(), _jsonl_errors():
            return self._load_all().get((namespace, fingerprint))

    def iter_nonterminal(self) -> list[WALEntry]:
        with self._with_lock(), _jsonl_errors():
            return [
                e
                for e in self._load_all().values()
                if e.state in (WALState.IN_FLIGHT, WALState.IN_DOUBT)
            ]

    def find_confirmed_fingerprint(self, namespace: str, pennylane_id: int) -> str | None:
        with self._with_lock(), _jsonl_errors():
            for e in self._load_all().values():
                if (
                    e.namespace == namespace
                    and e.state is WALState.CONFIRMED
                    and e.pennylane_id == pennylane_id
                ):
                    return e.fingerprint
        return None

    def transition_confirmed_if(
        self,
        namespace: str,
        fingerprint: str,
        expected_pennylane_id: int | None,
        to_state: WALState,
        reason: str,
    ) -> bool:
        """Conditionnelle par identité (D2 généralisée) — atomique sous le flock (le
        read-modify-write entier est dans la section critique cross-process)."""
        expected = None if expected_pennylane_id is None else int(expected_pennylane_id)
        with self._with_lock(), _jsonl_errors():
            entries = self._load_all()
            existing = entries.get((namespace, fingerprint))
            if (
                existing is None
                or existing.state is not WALState.CONFIRMED
                or existing.pennylane_id != expected
            ):
                return False  # mapping changé (re-confirmé / autre id / autre état) → no-op
            existing.state = to_state
            existing.pennylane_id = None
            existing.last_error = reason
            existing.updated_at = _utcnow_iso()
            entries[(namespace, fingerprint)] = existing
            self._persist_all(entries)
            return True


class _FlockGuard:
    """Context manager flock exclusif sur un lock-file stable (cross-process)."""

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._fd = -1

    def __enter__(self) -> _FlockGuard:
        self._fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1


class NullStore:
    """Store sans persistance — AUCUNE garantie d'idempotence (ADR §2.7).

    Émet un warning à l'instanciation. Réservé aux tests / à l'opt-out explicite ;
    la couche applicative DOIT injecter un store réel en production (§2.14 R1).
    """

    # Aucune persistance → aucune sérialisation cross-host.
    cross_host_safe: bool = False

    def __init__(self) -> None:
        warnings.warn(
            "NullStore : aucune garantie d'idempotence write (doublons possibles "
            "sur retry). Injecter un JsonlStore en production (ADR R1).",
            stacklevel=2,
        )

    def transition_confirmed_if(
        self,
        namespace: str,
        fingerprint: str,
        expected_pennylane_id: int | None,
        to_state: WALState,
        reason: str,
    ) -> bool:
        """Aucune persistance → aucun mapping à transitionner (no-op, ``False``)."""
        return False

    def reserve_in_flight(
        self,
        namespace: str,
        fingerprint: str,
        idempotency_key: str,
        *,
        readback_key: dict[str, Any] | None = None,
        id_watermark: int | None = None,
    ) -> ReserveResult:
        now = _utcnow_iso()
        entry = WALEntry(
            namespace=namespace,
            fingerprint=fingerprint,
            idempotency_key=idempotency_key,
            state=WALState.IN_FLIGHT,
            attempts=1,
            created_at=now,
            updated_at=now,
            readback_key=readback_key,
            id_watermark=id_watermark,
        )
        return ReserveResult(outcome=ReserveOutcome.PROCEEDING, entry=entry)

    def finalize(
        self,
        namespace: str,
        fingerprint: str,
        state: WALState,
        pennylane_id: int | None,
        last_error: str | None,
    ) -> WALEntry:
        now = _utcnow_iso()
        return WALEntry(
            namespace=namespace,
            fingerprint=fingerprint,
            idempotency_key=_new_idempotency_key(),
            state=state,
            pennylane_id=pennylane_id,
            last_error=last_error,
            created_at=now,
            updated_at=now,
        )

    def get(self, namespace: str, fingerprint: str) -> WALEntry | None:
        return None

    def iter_nonterminal(self) -> list[WALEntry]:
        return []

    def find_confirmed_fingerprint(self, namespace: str, pennylane_id: int) -> str | None:
        return None


# ---------------------------------------------------------------------------
# Store SQLite durable — DÉFAUT recommandé (UNIQUE(ns,fp) atomique, recover natif)
# ---------------------------------------------------------------------------


class SqliteIdempotencyStore:
    """Store d'idempotence SQLite (ADR) — store durable PAR DÉFAUT.

    Remplace ``JsonlStore`` comme store durable recommandé : l'unicité de la réservation
    est garantie par la clé primaire ``UNIQUE(namespace, fingerprint)`` et la
    sérialisation des writers du MÊME host par ``BEGIN IMMEDIATE`` (verrou write SQLite).
    Cohérent cross-process mono-host (plusieurs process, même fichier).

    Durabilité (CRUX §2.14) : ``journal_mode=WAL`` + ``synchronous=FULL`` → la transaction
    ``IN_FLIGHT`` est ``fsync`` **avant** le POST. ``busy_timeout`` : un 2ᵉ writer attend la
    fin de la transaction du 1ᵉʳ plutôt que d'échouer (``SQLITE_BUSY``).

    Une connexion est ouverte par opération (sûreté thread : ``WALManager`` appelle via
    ``anyio.to_thread`` — threads multiples) puis fermée.

    Mono-host : ``cross_host_safe=False``. Le scale multi-machines exige un store
    cross-host (Postgres ``UNIQUE``, côté app) injecté via le ``Protocol`` ;
    ``ensure_deployment_safety`` refuse sinon (jamais de doublon multi-machines en silence).
    """

    cross_host_safe: bool = False

    def __init__(self, path: Path | str, *, busy_timeout_ms: int = 5000) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = busy_timeout_ms
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._path,
            timeout=self._busy_timeout_ms / 1000,
            isolation_level=None,  # autocommit : on pilote BEGIN/COMMIT explicitement
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wal_entries (
                    namespace       TEXT    NOT NULL,
                    fingerprint     TEXT    NOT NULL,
                    idempotency_key TEXT    NOT NULL,
                    state           TEXT    NOT NULL,
                    pennylane_id    INTEGER,
                    attempts        INTEGER NOT NULL DEFAULT 0,
                    last_error      TEXT,
                    created_at      TEXT    NOT NULL DEFAULT '',
                    updated_at      TEXT    NOT NULL DEFAULT '',
                    readback_key    TEXT,
                    id_watermark    INTEGER,
                    PRIMARY KEY (namespace, fingerprint)
                )
                """
            )
            # Garde « écriture sœur » DÉCLARATIVE (NB3-3, revue v4) : UNIQUE(namespace,
            # pennylane_id) parmi les CONFIRMED → un même id confirmé sous un AUTRE fingerprint
            # lève une violation d'unicité (→ WALStoreError(conflict) → IN_DOUBT). Atomique,
            # impossible à oublier (contrairement au check applicatif TOCTOU précédent).
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_wal_confirmed_pid
                ON wal_entries (namespace, pennylane_id)
                WHERE state = 'CONFIRMED'
                """
            )

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> WALEntry:
        return WALEntry(
            namespace=row["namespace"],
            fingerprint=row["fingerprint"],
            idempotency_key=row["idempotency_key"],
            state=WALState(row["state"]),
            pennylane_id=row["pennylane_id"],
            attempts=int(row["attempts"]),
            last_error=row["last_error"],
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
            # NBI-2-RESIDUAL-1/2 : json.loads via le point UNIQUE _parse_readback_key (jamais nu).
            readback_key=_parse_readback_key(row["readback_key"]),
            id_watermark=row["id_watermark"],
        )

    def reserve_in_flight(
        self,
        namespace: str,
        fingerprint: str,
        idempotency_key: str,
        *,
        readback_key: dict[str, Any] | None = None,
        id_watermark: int | None = None,
    ) -> ReserveResult:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")  # verrou write : sérialise les writers
            try:
                row = conn.execute(
                    "SELECT * FROM wal_entries WHERE namespace = ? AND fingerprint = ?",
                    (namespace, fingerprint),
                ).fetchone()
                if row is not None and row["state"] == WALState.CONFIRMED.value:
                    conn.execute("COMMIT")
                    existing = self._row_to_entry(row)
                    return ReserveResult(
                        outcome=ReserveOutcome.SHORT_CIRCUIT,
                        entry=existing,
                        memorized_id=existing.pennylane_id,
                    )
                if row is not None and row["state"] in (
                    WALState.IN_FLIGHT.value,
                    WALState.IN_DOUBT.value,
                ):
                    conn.execute("COMMIT")
                    return ReserveResult(
                        outcome=ReserveOutcome.DUPLICATE, entry=self._row_to_entry(row)
                    )
                # absent / PENDING / FAILED → (re)commit IN_FLIGHT (rien n'a atterri).
                now = _utcnow_iso()
                attempts = (int(row["attempts"]) if row is not None else 0) + 1
                created_at = row["created_at"] if row is not None else now
                key = row["idempotency_key"] if row is not None else idempotency_key
                # NBI-2-RESIDUAL-2 : re-commit d'une ligne PENDING/FAILED — la colonne
                # readback_key existante peut être corrompue ; MÊME point de désérialisation que
                # le chemin READ (_parse_readback_key) → WALStoreError typé, jamais nu (§2.11).
                prev_rb = _parse_readback_key(row["readback_key"] if row is not None else None)
                rb_key = readback_key if readback_key is not None else prev_rb
                watermark = (
                    id_watermark
                    if id_watermark is not None
                    else (row["id_watermark"] if row is not None else None)
                )
                conn.execute(
                    """
                    INSERT INTO wal_entries (
                        namespace, fingerprint, idempotency_key, state, pennylane_id,
                        attempts, last_error, created_at, updated_at, readback_key, id_watermark
                    ) VALUES (?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?, ?)
                    ON CONFLICT(namespace, fingerprint) DO UPDATE SET
                        idempotency_key = excluded.idempotency_key,
                        state           = excluded.state,
                        pennylane_id    = NULL,
                        attempts        = excluded.attempts,
                        last_error      = NULL,
                        updated_at      = excluded.updated_at,
                        readback_key    = excluded.readback_key,
                        id_watermark    = excluded.id_watermark
                    """,
                    (
                        namespace,
                        fingerprint,
                        key,
                        WALState.IN_FLIGHT.value,
                        attempts,
                        created_at,
                        now,
                        json.dumps(rb_key) if rb_key is not None else None,
                        watermark,
                    ),
                )
                conn.execute("COMMIT")
                entry = WALEntry(
                    namespace=namespace,
                    fingerprint=fingerprint,
                    idempotency_key=key,
                    state=WALState.IN_FLIGHT,
                    attempts=attempts,
                    created_at=created_at or now,
                    updated_at=now,
                    readback_key=rb_key,
                    id_watermark=watermark,
                )
                return ReserveResult(outcome=ReserveOutcome.PROCEEDING, entry=entry)
            except sqlite3.Error as exc:
                # CI-2 : driver SQLite ne fuit jamais nu hors-contrat §2.11. La violation
                # UNIQUE(namespace, pennylane_id) WHERE CONFIRMED (IntegrityError) = garde
                # écriture-sœur (NB3-3) → conflict=True → l'appelant bascule IN_DOUBT.
                conn.execute("ROLLBACK")
                raise WALStoreError(
                    f"SQLite store error: {exc}",
                    conflict=isinstance(exc, sqlite3.IntegrityError),
                ) from exc
            except BaseException:
                if conn.in_transaction:  # un ROLLBACK aveugle masquerait l'erreur d'origine
                    conn.execute("ROLLBACK")
                raise

    def finalize(
        self,
        namespace: str,
        fingerprint: str,
        state: WALState,
        pennylane_id: int | None,
        last_error: str | None,
    ) -> WALEntry:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM wal_entries WHERE namespace = ? AND fingerprint = ?",
                    (namespace, fingerprint),
                ).fetchone()
                now = _utcnow_iso()
                if row is None:
                    # Défensif : finalize sans réservation préalable.
                    key = _new_idempotency_key()
                    conn.execute(
                        """
                        INSERT INTO wal_entries (
                            namespace, fingerprint, idempotency_key, state, pennylane_id,
                            attempts, last_error, created_at, updated_at, readback_key, id_watermark
                        ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, NULL, NULL)
                        """,
                        (
                            namespace,
                            fingerprint,
                            key,
                            state.value,
                            pennylane_id,
                            last_error,
                            now,
                            now,
                        ),
                    )
                    conn.execute("COMMIT")
                    return WALEntry(
                        namespace=namespace,
                        fingerprint=fingerprint,
                        idempotency_key=key,
                        state=state,
                        pennylane_id=pennylane_id,
                        last_error=last_error,
                        created_at=now,
                        updated_at=now,
                    )
                conn.execute(
                    """
                    UPDATE wal_entries
                    SET state = ?, pennylane_id = ?, last_error = ?, updated_at = ?
                    WHERE namespace = ? AND fingerprint = ?
                    """,
                    (state.value, pennylane_id, last_error, now, namespace, fingerprint),
                )
                conn.execute("COMMIT")
                entry = self._row_to_entry(row)
                entry.state = state
                entry.pennylane_id = pennylane_id
                entry.last_error = last_error
                entry.updated_at = now
                return entry
            except sqlite3.Error as exc:
                # CI-2 : driver SQLite ne fuit jamais nu hors-contrat §2.11. La violation
                # UNIQUE(namespace, pennylane_id) WHERE CONFIRMED (IntegrityError) = garde
                # écriture-sœur (NB3-3) → conflict=True → l'appelant bascule IN_DOUBT.
                conn.execute("ROLLBACK")
                raise WALStoreError(
                    f"SQLite store error: {exc}",
                    conflict=isinstance(exc, sqlite3.IntegrityError),
                ) from exc
            except BaseException:
                if conn.in_transaction:  # un ROLLBACK aveugle masquerait l'erreur d'origine
                    conn.execute("ROLLBACK")
                raise

    def get(self, namespace: str, fingerprint: str) -> WALEntry | None:
        # NBI-2 : une méthode READ ne fuit jamais d'erreur driver nue (§2.11) — recover()/
        # resolve() l'attrapent comme WALStoreError → IN_DOUBT/store_error, jamais de boot
        # brické sur une panne SQLite transitoire. conflict=False (lecture, pas d'unicité).
        try:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT * FROM wal_entries WHERE namespace = ? AND fingerprint = ?",
                    (namespace, fingerprint),
                ).fetchone()
                return self._row_to_entry(row) if row is not None else None
        except sqlite3.Error as exc:
            raise WALStoreError(f"SQLite store error (get): {exc}", conflict=False) from exc

    def iter_nonterminal(self) -> list[WALEntry]:
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT * FROM wal_entries WHERE state IN (?, ?)",
                    (WALState.IN_FLIGHT.value, WALState.IN_DOUBT.value),
                ).fetchall()
                return [self._row_to_entry(r) for r in rows]
        except sqlite3.Error as exc:
            raise WALStoreError(
                f"SQLite store error (iter_nonterminal): {exc}", conflict=False
            ) from exc

    def find_confirmed_fingerprint(self, namespace: str, pennylane_id: int) -> str | None:
        try:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    """
                    SELECT fingerprint FROM wal_entries
                    WHERE namespace = ? AND pennylane_id = ? AND state = ?
                    LIMIT 1
                    """,
                    (namespace, pennylane_id, WALState.CONFIRMED.value),
                ).fetchone()
                return row["fingerprint"] if row is not None else None
        except sqlite3.Error as exc:
            raise WALStoreError(
                f"SQLite store error (find_confirmed_fingerprint): {exc}", conflict=False
            ) from exc

    def transition_confirmed_if(
        self,
        namespace: str,
        fingerprint: str,
        expected_pennylane_id: int | None,
        to_state: WALState,
        reason: str,
    ) -> bool:
        """Conditionnelle par identité (D2 généralisée) — UN SEUL ``UPDATE`` conditionnel
        SQL (``WHERE state='CONFIRMED' AND pennylane_id IS ?``), contrôlé par rows-affected :
        atomique cross-process sans dépendre d'aucun verrou applicatif. ``IS ?`` couvre les
        deux identités : un id précis, ou l'ABSENCE d'id (``None`` → ``IS NULL``) — le cas des
        writes ``204 No Content`` (rapprochement, statut) confirmés sans id, que ``= ?``
        ne pouvait jamais atteindre (``NULL = NULL`` est faux en SQL)."""
        expected = None if expected_pennylane_id is None else int(expected_pennylane_id)
        try:
            with closing(self._connect()) as conn:
                cur = conn.execute(
                    """
                    UPDATE wal_entries
                    SET state = ?, pennylane_id = NULL, last_error = ?, updated_at = ?
                    WHERE namespace = ? AND fingerprint = ?
                      AND state = ? AND pennylane_id IS ?
                    """,
                    (
                        to_state.value,
                        reason,
                        _utcnow_iso(),
                        namespace,
                        fingerprint,
                        WALState.CONFIRMED.value,
                        expected,
                    ),
                )
                conn.commit()
                return cur.rowcount == 1
        except sqlite3.Error as exc:
            raise WALStoreError(
                f"SQLite store error (transition_confirmed_if): {exc}", conflict=False
            ) from exc


class DeploymentUnsafeError(PennylaneError):
    """Déploiement multi-writer/multi-machines avec un store mono-host (ADR).

    Levée par ``ensure_deployment_safety`` au démarrage : un store ``cross_host_safe=False``
    (SQLite/JSONL) ne sérialise pas les écritures entre machines → un doublon
    multi-machines pourrait passer EN SILENCE. On échoue bruyamment au démarrage plutôt
    que de risquer un doublon comptable invisible.
    """

    def __init__(self, store_type: str) -> None:
        super().__init__(
            f"Déploiement multi-writer refusé : le store « {store_type} » n'est pas "
            "cross-host (cross_host_safe=False). En multi-machines, injecter un store "
            "cross-host (p. ex. Postgres UNIQUE(namespace, fingerprint)) avant d'activer "
            "plusieurs writers.",
            status_code=None,
            hint=(
                "Mono-machine (self-hosted) : SQLite suffit. Multi-machines : injecter un "
                "IdempotencyStore cross_host_safe=True (Postgres) via le Protocol."
            ),
        )
        self.store_type = store_type


class WALStoreRequiredError(PennylaneError):
    """Write tenté sans store WAL réel (ADR, STORE-1) — DISTINCT de
    ``DeploymentUnsafeError`` (qui vise le multi-writer cross-host). Un ``create()`` (POST)
    adossé à un ``NullStore`` (no-op) ou à un store absent n'offre AUCUNE garantie
    anti-doublon : on échoue fail-fast AVANT le moindre effet plutôt que d'écrire à l'aveugle.
    Message dédié (pas « multi-writer refusé ») pour ne pas égarer l'opérateur."""

    def __init__(self) -> None:
        super().__init__(
            "create() (write) sans store WAL réel : aucune garantie anti-doublon. Injecter "
            "LedgerEntriesResource(client, wal_store=<SqliteIdempotencyStore|PostgresStore>) "
            "— NullStore interdit en écriture (réservé lecture seule / tests).",
            status_code=None,
            hint=(
                "Câbler le store réel au lifespan write (DT-063) AVANT d'activer create_entry : "
                "SqliteIdempotencyStore (mono-machine) ou PostgresStore (cross-host)."
            ),
        )


def ensure_deployment_safety(store: IdempotencyStore, *, multi_writer: bool) -> None:
    """Garde de démarrage (ADR, zéro-dette) : refuse un déploiement
    multi-writer/multi-machines tant que le store injecté n'est pas cross-host.

    Mono-machine (``multi_writer=False``) : tout store convient. Multi-machines
    (``multi_writer=True``) : exige ``store.cross_host_safe`` — sinon ``DeploymentUnsafeError``.
    Un store mono-host ne peut ainsi JAMAIS causer un doublon multi-machines en silence.
    """
    if multi_writer and not getattr(store, "cross_host_safe", False):
        raise DeploymentUnsafeError(type(store).__name__)


# ---------------------------------------------------------------------------
# Manager — verrou intra-process keyé (namespace, fingerprint)
# ---------------------------------------------------------------------------


class _Reservation:
    """Poignée de réservation yieldée par ``WALManager.reserve`` (interne)."""

    def __init__(
        self, manager: WALManager, namespace: str, fingerprint: str, result: ReserveResult
    ) -> None:
        self._manager = manager
        self.namespace = namespace
        self.fingerprint = fingerprint
        self._result = result
        # True dès qu'une transition terminale/IN_DOUBT a eu lieu. Le garde de reserve()
        # bascule IN_DOUBT si la section sort sans résolution (anti IN_FLIGHT orphelin).
        self._resolved = False

    @property
    def resolved(self) -> bool:
        return self._resolved

    @property
    def should_short_circuit(self) -> bool:
        return self._result.outcome is ReserveOutcome.SHORT_CIRCUIT

    @property
    def is_duplicate_in_progress(self) -> bool:
        return self._result.outcome is ReserveOutcome.DUPLICATE

    @property
    def should_proceed(self) -> bool:
        return self._result.outcome is ReserveOutcome.PROCEEDING

    @property
    def memorized_id(self) -> int | None:
        return self._result.memorized_id

    @property
    def duplicate_state(self) -> WALState:
        return self._result.entry.state

    @property
    def memorized_updated_at(self) -> str:
        """Horodatage ISO de la DERNIÈRE transition de l'entrée mémorisée (= l'instant du
        confirm pour un SHORT_CIRCUIT) — garde d'âge RES-1 : un double-404 sur une écriture
        confirmée RÉCEMMENT est suspect (lag read-after-write), pas une suppression."""
        return self._result.entry.updated_at

    @property
    def idempotency_key(self) -> str:
        return self._result.entry.idempotency_key

    @property
    def created_at(self) -> str:
        """Horodatage ISO de la réservation IN_FLIGHT — borne basse de la fenêtre
        created_at du read-back (garde faux-1-match, revue Cs 4 #4)."""
        return self._result.entry.created_at

    async def confirm(self, pennylane_id: int | None) -> WALEntry:
        # `pennylane_id=None` : opération CONFIRMED SANS id serveur (write 204 No Content —
        # matched_transactions / payment_status / mark_as_paid, B-K9). La garde écriture-sœur
        # `finalize` est inerte (skip si pennylane_id is None) et l'index UNIQUE(ns, pennylane_id)
        # WHERE CONFIRMED autorise plusieurs NULL (NULLs distincts en SQLite) — chaque opération
        # est dédupliquée par son (namespace, fingerprint), pas par l'id. `POST /ledger_entries`
        # passe toujours un int (201 + id) — rétrocompatibilité totale.
        # _resolved APRÈS le succès durable (revue v2 medium 1) : si _finalize lève, la
        # garde finally de reserve() bascule IN_DOUBT (jamais d'IN_FLIGHT orphelin).
        entry = await self._manager._finalize(
            self.namespace, self.fingerprint, WALState.CONFIRMED, pennylane_id, None
        )
        self._resolved = True
        return entry

    async def fail(self, error: str) -> WALEntry:
        entry = await self._manager._finalize(
            self.namespace, self.fingerprint, WALState.FAILED, None, error
        )
        self._resolved = True
        return entry

    async def mark_in_doubt(self, error: str) -> WALEntry:
        entry = await self._manager._finalize(
            self.namespace, self.fingerprint, WALState.IN_DOUBT, None, error
        )
        self._resolved = True
        return entry


class WALManager:
    """Coordonne verrou intra-process (anyio.Lock keyé (ns,fp)) + store durable.

    Interne (§2.11) : non exporté. La resource ``LedgerEntriesResource`` en tient
    une instance partagée (les verrous persistent entre appels ``create()``).
    """

    def __init__(self, store: IdempotencyStore) -> None:
        self._store = store
        self._locks: dict[str, anyio.Lock] = {}
        self._locks_guard = anyio.Lock()

    async def _lock_for(self, namespace: str, fingerprint: str) -> anyio.Lock:
        # Clé (ns,fp) ; jamais retirée du dict (§2.14 : pas de race lock-registry).
        key = f"{namespace}\x00{fingerprint}"
        async with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = anyio.Lock()
                self._locks[key] = lock
            return lock

    async def _finalize(
        self,
        namespace: str,
        fingerprint: str,
        state: WALState,
        pennylane_id: int | None,
        last_error: str | None,
    ) -> WALEntry:
        call = functools.partial(
            self._store.finalize,
            namespace,
            fingerprint,
            state,
            pennylane_id,
            last_error,
        )
        return await anyio.to_thread.run_sync(call)

    async def iter_nonterminal(self) -> list[WALEntry]:
        """Entrées non terminales (``IN_FLIGHT`` / ``IN_DOUBT``) — ``recover()`` au
        démarrage (ADR)."""
        return await anyio.to_thread.run_sync(self._store.iter_nonterminal)

    async def find_confirmed_fingerprint(self, namespace: str, pennylane_id: int) -> str | None:
        """Fingerprint d'un ``CONFIRMED`` portant ``pennylane_id`` (garde écriture
        sœur, ADR) — ``None`` si aucun."""
        call = functools.partial(self._store.find_confirmed_fingerprint, namespace, pennylane_id)
        return await anyio.to_thread.run_sync(call)

    async def recover_in_doubt(self, namespace: str, fingerprint: str, reason: str) -> WALEntry:
        """Reprise (``recover()``) : transition vers ``IN_DOUBT`` (réconciliation non
        concluante) — résolution humaine ultérieure via ``resolve()``. Verrou tenu."""
        lock = await self._lock_for(namespace, fingerprint)
        async with lock:
            return await self._finalize(namespace, fingerprint, WALState.IN_DOUBT, None, reason)

    async def resolve(
        self,
        *,
        namespace: str,
        fingerprint: str,
        landed: bool,
        pennylane_id: int | None = None,
    ) -> WALEntry:
        """Résolution HUMAINE d'un `IN_DOUBT` (ADR) — transition technique.

        `landed=True` → `CONFIRMED` (mappe `pennylane_id`) ; `landed=False` → `FAILED`
        (re-POST de la même op autorisé ensuite). Verrou `(ns, fingerprint)` tenu. Le
        FLUX humain (présenter les candidats du read-back, capturer le oui/non) est
        applicatif (hors lib, zéro-couplage) ; cette méthode n'est QUE la transition.

        Garde-fous (revue RB-3#6 + F1 review locale 2026-06-20) : `pennylane_id` obligatoire
        si `landed=True` UNIQUEMENT sur une écriture à id serveur (`readback_key` présent =
        écriture ledger 201+id : un CONFIRMED sans id y serait un fantôme). Les ops ACK-ONLY
        204-no-id (`readback_key` None : match_payment / update_invoice_status) confirment
        légitimement avec `pennylane_id=None` — symétrie `confirm(None)`. L'entrée doit EXISTER
        et être `IN_DOUBT` (jamais de CONFIRMED/FAILED ex-nihilo qui ferait court-circuiter un
        futur create() sans écriture réelle).
        """
        lock = await self._lock_for(namespace, fingerprint)
        async with lock:
            existing = await anyio.to_thread.run_sync(self._store.get, namespace, fingerprint)
            if existing is None or existing.state is not WALState.IN_DOUBT:
                state = existing.state.value if existing is not None else None
                raise ValueError(
                    f"resolve() : entrée absente ou non-IN_DOUBT (état={state}) — "
                    "rien à résoudre (jamais de transition ex-nihilo)."
                )
            # F1 : `pennylane_id` exigé pour `landed=True` SEULEMENT sur une écriture à id
            # serveur (readback_key présent → ledger). Les writes ack-only 204-no-id
            # (readback_key None) confirment avec id=None (sinon IN_DOUBT non résolvable →
            # re-POST → double rapprochement/statut sur le chemin argent).
            if landed and pennylane_id is None and existing.readback_key is not None:
                raise ValueError(
                    "resolve(landed=True) exige pennylane_id pour une écriture à id serveur "
                    "(readback_key présent) — un CONFIRMED sans id y serait un fantôme."
                )
            if landed:
                return await self._finalize(
                    namespace, fingerprint, WALState.CONFIRMED, pennylane_id, None
                )
            return await self._finalize(
                namespace,
                fingerprint,
                WALState.FAILED,
                None,
                "résolu FAILED (réconciliation humaine : non atterri)",
            )

    async def invalidate_confirmed(
        self,
        namespace: str,
        fingerprint: str,
        *,
        expected_pennylane_id: int | None,
        reason: str,
    ) -> bool:
        """Invalide un mapping ``CONFIRMED`` dont le ``pennylane_id`` n'existe PLUS côté
        Pennylane (suppression via l'UI — l'API V2 n'expose AUCUN ``DELETE /ledger_entries``,
        vérifié spec OpenAPI + sonde réelle, campagne 2026-06-10).

        **CONDITIONNELLE par IDENTITÉ et ATOMIQUE store-level (revue (c) D2)** : la
        transition ``CONFIRMED → FAILED`` n'a lieu que si l'entrée porte ENCORE
        ``expected_pennylane_id`` — un seul UPDATE conditionnel
        (``WHERE state='CONFIRMED' AND pennylane_id=?``) côté store, contrôlé par
        rows-affected. Sous concurrence, le perdant (qui tient un dead-id périmé) ne peut
        JAMAIS invalider un mapping re-confirmé sur un AUTRE id vivant — l'atomicité tient
        cross-process (pas seulement via le verrou intra-process tenu ici).

        Retourne ``True`` si invalidé (re-soumission du même fingerprint RÉ-ÉCRIT ensuite,
        sémantique ``FAILED``), ``False`` si le mapping a changé entre-temps (re-confirmé,
        autre id, autre état) — l'appelant re-tente alors sa réservation (le court-circuit
        verra le mapping FRAIS)."""
        return await self._transition_confirmed(
            namespace, fingerprint, expected_pennylane_id, WALState.FAILED, reason
        )

    async def doubt_confirmed(
        self,
        namespace: str,
        fingerprint: str,
        *,
        expected_pennylane_id: int | None,
        reason: str,
    ) -> bool:
        """Bascule GARDÉE ``CONFIRMED → IN_DOUBT`` (conditionnelle par identité, atomique
        store-level — RES-1/RES-2 revue (c)) : utilisée quand un double-404 frappe une
        écriture qui POURRAIT être vivante (confirmée récemment → lag read-after-write
        possible) ou en épuisement de rejeu — l'humain tranche via ``resolve()``
        (``landed=True`` + id si elle existe, ``landed=False`` sinon → ré-écriture).
        JAMAIS de transition aveugle : ``False`` si le mapping a changé entre-temps."""
        return await self._transition_confirmed(
            namespace, fingerprint, expected_pennylane_id, WALState.IN_DOUBT, reason
        )

    async def _transition_confirmed(
        self,
        namespace: str,
        fingerprint: str,
        expected_pennylane_id: int | None,
        to_state: WALState,
        reason: str,
    ) -> bool:
        lock = await self._lock_for(namespace, fingerprint)
        async with lock:
            call = functools.partial(
                self._store.transition_confirmed_if,
                namespace,
                fingerprint,
                expected_pennylane_id,
                to_state,
                reason,
            )
            return await anyio.to_thread.run_sync(call)

    @asynccontextmanager
    async def reserve(
        self,
        *,
        namespace: str,
        payload: Mapping[str, Any],
        readback_key: dict[str, Any] | None = None,
        id_watermark: int | None = None,
    ) -> AsyncIterator[_Reservation]:
        """Section critique intra-process : verrou (ns,fp) tenu sur tout le bloc
        (POST de l'appelant compris). À l'entrée, ``IN_FLIGHT`` est déjà durable
        (si PROCEEDING) — le POST peut partir (CRUX §2.14). ``readback_key`` +
        ``id_watermark`` sont persistés avec ``IN_FLIGHT`` (avant le POST) pour
        ``recover()`` (ADR)."""
        fingerprint = compute_fingerprint(payload)
        lock = await self._lock_for(namespace, fingerprint)
        async with lock:
            key = _new_idempotency_key()
            call = functools.partial(
                self._store.reserve_in_flight,
                namespace,
                fingerprint,
                key,
                readback_key=readback_key,
                id_watermark=id_watermark,
            )
            result = await anyio.to_thread.run_sync(call)
            resv = _Reservation(self, namespace, fingerprint, result)
            try:
                yield resv
            finally:
                # Garde transversal (revue RB-3#3c / §2.14) : si IN_FLIGHT a été écrit
                # (PROCEEDING) mais qu'AUCUNE transition terminale n'a eu lieu — sortie du
                # bloc via une exception non gérée — basculer IN_DOUBT. JAMAIS d'IN_FLIGHT
                # orphelin (qui bloquerait toute re-soumission à jamais, état faux).
                if result.outcome is ReserveOutcome.PROCEEDING and not resv.resolved:
                    try:
                        await self._finalize(
                            namespace,
                            fingerprint,
                            WALState.IN_DOUBT,
                            None,
                            "sortie reserve() sans transition (garde anti-orphelin)",
                        )
                    except WALStoreError:
                        # CI-2 : store indisponible PENDANT la garde → ne pas masquer
                        # l'exception d'origine. L'entrée reste IN_FLIGHT → recover() la
                        # traitera (ré-entrant). Best-effort.
                        pass
