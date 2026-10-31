"""Modèle métier de l'endpoint `/ledger_entries` regroupées (v0.3 B.5).

**Distinct** de `LedgerEntryLine` (modèle v0.1 ciblant `/ledger_entry_lines`
plat — lignes individuelles avec contexte entry imbriqué).

`LedgerEntryGrouped` représente une **écriture comptable complète**
(en-tête) : id, label, date, journal, références (invoice/piece),
métadonnées attachement et timestamps.

Schéma capturé empiriquement 2026-05-12 SID `6afe82d0` :

    {"id": int, "label": str, "date": "YYYY-MM-DD",
     "journal_id": int, "journal": {"id": int, "url": str},
     "invoice_number": str, "piece_number": str,
     "due_date": "YYYY-MM-DD",
     "attachment": Any | None,
     "categories": list | None,
     "ledger_attachment_filename": str | None,
     "created_at": "ISO timestamp", "updated_at": "ISO timestamp"}

13 champs capturés. `journal` peut coexister avec `journal_id` (legacy +
2026 simultanés en phase Sunset — pattern observé sur lignes plates,
cohérent ici).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class LedgerEntryGrouped(BaseModel):
    """Écriture comptable regroupée exposée par `/ledger_entries`.

    Distinct de `LedgerEntryLine` (`/ledger_entry_lines` plat v0.1).
    Tous champs sauf `id` optionnels — résilience aux variations
    par type de journal (banque/ventes/achats) et au Sunset 2026.
    """

    model_config = ConfigDict(extra="allow")

    id: int
    label: str | None = None
    date: str | None = None
    journal_id: int | None = None
    journal: dict[str, Any] | None = None
    invoice_number: str | None = None
    piece_number: str | None = None
    due_date: str | None = None
    attachment: Any = None
    categories: list[Any] | None = None
    ledger_attachment_filename: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
