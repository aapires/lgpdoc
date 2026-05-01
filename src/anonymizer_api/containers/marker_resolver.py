"""Container-scoped marker resolution.

Given an ``(entity_type, original_text)`` pair detected within a
container, the resolver returns the marker (e.g. ``[PESSOA_0001]``) that
the container has assigned to that real value — creating one if the
value has not been seen before.

Invariants enforced here:

* The same normalised value within the same container always resolves
  to the same marker. (Tested in ``test_marker_resolver``.)
* Marker indices are allocated per-container, per-label. ``[PESSOA_0001]``
  in container A and ``[PESSOA_0001]`` in container B are independent
  identifiers.
* Markers themselves are unique within a container (DB constraint plus
  per-label index counter).
* Markers never carry semantic hints from the real value — labels come
  from a fixed dictionary (``LABEL_FOR_ENTITY_TYPE``) and indices are
  zero-padded sequential numbers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from ..db.models import ContainerMappingEntryModel
from ..db.repositories import ContainerMappingEntryRepository
from .normalizers import normalize

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Label dictionary — entity_type → marker label.
#
# IMPORTANT: never mix the real value into the label. Use ``DOCUMENTO`` as
# the catch-all for unknown / generic identifiers; do NOT fall back to
# the entity type as label (that could leak intent).
# ---------------------------------------------------------------------------

_DEFAULT_LABEL = "DOCUMENTO"

LABEL_FOR_ENTITY_TYPE: dict[str, str] = {
    # PII categories
    "private_person": "PESSOA",
    # Legal entities (companies + government bodies). Distinct marker
    # from PESSOA so reviewers don't confuse natural with juridical
    # persons in the same container's mapping table.
    "private_company": "PESSOA_JUR",
    "private_email": "EMAIL",
    "private_phone": "TELEFONE",
    "private_address": "ENDERECO",
    "private_date": "DATA",
    "private_url": "URL",
    "account_number": "CONTA_BANCARIA",
    # Brazilian identifiers
    "cpf": "CPF",
    "cnpj": "CNPJ",
    "rg": "RG",
    "cnh": "CNH",
    "passaporte": "PASSAPORTE",
    "titulo_eleitor": "TITULO_ELEITOR",
    "pis": "PIS",
    "ctps": "CTPS",
    "sus": "SUS",
    "oab": "OAB",
    "crm": "CRM",
    "crea": "CREA",
    "placa": "PLACA",
    "renavam": "RENAVAM",
    "processo_cnj": "PROCESSO",
    "inscricao_estadual": "INSCRICAO_ESTADUAL",
    "cep": "CEP",
    "ip": "IP",
    "financeiro": "VALOR",
    "secret": "SEGREDO",
}


def label_for(entity_type: str) -> str:
    """Return the marker label for ``entity_type``. Unknown types fall
    back to ``DOCUMENTO`` so we never emit a label derived from the
    real value (e.g. ``[JOAO_SILVA]``)."""
    return LABEL_FOR_ENTITY_TYPE.get(entity_type, _DEFAULT_LABEL)


# Width of the marker counter — ``[PESSOA_0001]``. Four digits comfortably
# covers any single container; on overflow the counter just keeps growing
# (``[PESSOA_10000]``), no wrap-around.
_INDEX_WIDTH = 4


def format_marker(label: str, index: int) -> str:
    """Build a marker string from label + index. Pure function — no DB
    access; useful in tests."""
    return f"[{label}_{index:0{_INDEX_WIDTH}d}]"


@dataclass(frozen=True)
class ResolvedMarker:
    """The marker the resolver picked, plus the persisted entry behind
    it. ``created`` is True when this call inserted a new entry."""

    marker: str
    mapping_entry: ContainerMappingEntryModel
    created: bool


class MarkerResolver:
    """Bound to a single ``container_id`` so callers cannot accidentally
    leak across containers. Construct one per upload."""

    def __init__(
        self,
        repo: ContainerMappingEntryRepository,
        container_id: str,
    ) -> None:
        self.repo = repo
        self.container_id = container_id

    def resolve(
        self,
        *,
        entity_type: str,
        original_text: str,
        detection_source: str | None = None,
        document_id: str | None = None,
    ) -> ResolvedMarker:
        """Return the marker for ``original_text`` inside this container.

        If a mapping entry already exists for the same
        ``(container_id, entity_type, normalized_value)`` triple, reuse
        its marker (and bump ``last_seen_at``). Otherwise allocate the
        next index for the label and persist a new entry.

        Empty normalisations (e.g. a CPF detection that resolves to no
        digits at all) fall back to using ``original_text`` as the
        normalised key, so the entry is still uniquely identifiable.
        """
        normalized = normalize(entity_type, original_text)
        if not normalized:
            # Defensive fallback — without a normalized key two
            # detections of the same garbage value would never match.
            # Use the trimmed original so equality is still possible.
            normalized = original_text.strip()

        existing = self.repo.find_by_normalized(
            container_id=self.container_id,
            entity_type=entity_type,
            normalized_value=normalized,
        )
        if existing is not None:
            self.repo.touch_last_seen(existing)
            # Metadata only — never log the marker itself; the marker is
            # the public identifier in the persisted artefact, but it
            # encodes the entity_type plus an index, both of which are
            # available as separate fields here.
            logger.debug(
                "Marker reused container_id=%s entity_type=%s entry_id=%d",
                self.container_id,
                entity_type,
                existing.id,
            )
            return ResolvedMarker(
                marker=existing.marker,
                mapping_entry=existing,
                created=False,
            )

        label = label_for(entity_type)
        next_idx = self.repo.max_index_for_label(self.container_id, label) + 1
        marker = format_marker(label, next_idx)

        now = datetime.now(timezone.utc)
        entry = self.repo.create(
            container_id=self.container_id,
            entity_type=entity_type,
            marker=marker,
            original_text=original_text,
            normalized_value=normalized,
            review_status="auto",
            detection_source=detection_source,
            created_from_document_id=document_id,
            first_seen_at=now,
            last_seen_at=now,
        )
        logger.info(
            "Marker created container_id=%s entity_type=%s entry_id=%d "
            "detection_source=%s document_id=%s",
            self.container_id,
            entity_type,
            entry.id,
            detection_source or "-",
            document_id or "-",
        )
        return ResolvedMarker(
            marker=marker, mapping_entry=entry, created=True
        )
