"""XLSX export of a container's mapping table — sensitive variant only.

The export mirrors the visible mapping table (``Marcador``, ``Tipo``,
``Valor real``, ``Valor normalizado``, ``Ocorrências``, ``Revisão``)
plus the ``Valor real`` column with the original text. That extra
column is the whole reason the export exists: lets the operator
reverse markers back to real values when reidentification is
explicitly required. Treat the file as a copy of the mapping table —
share only in trusted environments.
"""
from __future__ import annotations

import io
import logging

from openpyxl import Workbook

from ..db.models import ContainerMappingEntryModel
from ..db.repositories import ContainerMappingEntryRepository

logger = logging.getLogger(__name__)


# Column order mirrors the visible mapping page (Marcador / Tipo /
# Valor normalizado / Ocorrências / Revisão), with ``Valor real``
# inserted right after the normalised value — the extra column is
# what makes this export "sensitive".
_SENSITIVE_HEADERS = [
    "Marcador",
    "Tipo",
    "Valor normalizado",
    "Valor real",
    "Ocorrências",
    "Revisão",
]


def _row_sensitive(
    entry: ContainerMappingEntryModel,
    occurrences: list[tuple[str, str]],
) -> list[str | int]:
    """Build one sensitive row.

    ``occurrences`` is a list of ``(document_id, filename)`` tuples;
    we render only the filenames (joined by ``; ``) so the cell stays
    readable in Excel without exposing internal IDs.
    """
    occurrence_text = "; ".join(filename for _doc_id, filename in occurrences)
    return [
        entry.marker,
        entry.entity_type,
        entry.normalized_value,
        entry.original_text,
        occurrence_text,
        entry.review_status,
    ]


def _wb_to_bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_sensitive_xlsx(
    *,
    container_id: str,
    repo: ContainerMappingEntryRepository,
) -> bytes:
    """Sensitive XLSX — INCLUDES ``original_text`` in the ``Valor real``
    column. Routed only by the explicit ``/export-sensitive.xlsx``
    endpoint, which the UI gates behind a confirmation dialog."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "mapping_sensitive"
    ws.append(_SENSITIVE_HEADERS)

    rows = repo.list_for_container(container_id)
    occurrences = repo.list_occurrences_by_entry(
        container_id, [r.id for r in rows]
    )
    for entry in rows:
        ws.append(_row_sensitive(entry, occurrences.get(entry.id, [])))

    payload = _wb_to_bytes(wb)
    logger.info(
        "Sensitive mapping export built container_id=%s entries=%d bytes=%d",
        container_id,
        len(rows),
        len(payload),
    )
    return payload
