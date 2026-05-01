"""Promote an approved job's applied spans into a container's marker
table.

This is the bridge between the regular review flow (which lives in
``JobService`` and produces job-local indexed markers like
``[PESSOA_01]``) and the container's global marker space (where the
same person across two documents shares the same ``[PESSOA_0001]``
identifier).

Inputs:
* ``applied_spans`` — the list persisted at ``job.spans_path``,
  filtered to drop entries marked as false positives by the reviewer.
* ``redacted_text`` — the text the reviewer signed off on, with the
  job-local placeholders in place.

Outputs:
* A new pseudonymised text where every job placeholder has been
  swapped for the corresponding container marker.
* A list of ``ContainerSpan`` rows (already in dict shape, ready for
  ``ContainerSpanRepository.add_many``).
* The marker resolver records the entries in
  ``ContainerMappingEntryModel`` along the way.

This module is invoked from ``JobService.approve`` via a hook the
router wires up — kept here so the container code is responsible for
its own glue, never the other way around.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..db.repositories import (
    ContainerMappingEntryRepository,
    ContainerSpanRepository,
)
from .marker_resolver import MarkerResolver

logger = logging.getLogger(__name__)


@dataclass
class PromotionResult:
    pseudonymized_text: str
    span_count: int
    new_markers: int
    reused_markers: int


def _normalise_for_replace(text: str, mapping: dict[str, str]) -> str:
    """Replace each job placeholder in ``text`` with its container
    marker. Sort keys longest-first so e.g. ``[PESSOA_10]`` wins over
    ``[PESSOA_1]`` when both happen to exist."""
    out = text
    for job_marker in sorted(mapping.keys(), key=len, reverse=True):
        out = out.replace(job_marker, mapping[job_marker])
    return out


def promote_job_spans_to_container(
    *,
    container_id: str,
    document_id: str,
    redacted_text: str,
    applied_spans: list[dict[str, Any]],
    mapping_repo: ContainerMappingEntryRepository,
    span_repo: ContainerSpanRepository,
) -> PromotionResult:
    """Build container markers + spans for an approved job.

    Spans flagged ``false_positive=True`` are skipped: their original
    text was restored in the redacted file and the placeholder no
    longer exists, so there's nothing to promote.
    """
    resolver = MarkerResolver(mapping_repo, container_id)

    # Map: job-local placeholder string -> container marker
    placeholder_to_marker: dict[str, str] = {}
    container_spans: list[dict[str, Any]] = []
    new_markers = 0
    reused_markers = 0

    for span in applied_spans:
        if span.get("false_positive"):
            continue
        original = span.get("original_text")
        entity_type = span.get("entity_type")
        job_placeholder = span.get("replacement")
        if not original or not entity_type or not job_placeholder:
            # Defensive — older payloads might miss fields. Skip without
            # raising; promotion shouldn't fail because of one stale row.
            continue

        # Reuse the same container marker for repeated occurrences of
        # the same job placeholder (Redactor's indexed strategy already
        # collapses repeats into one placeholder string).
        if job_placeholder not in placeholder_to_marker:
            resolved = resolver.resolve(
                entity_type=entity_type,
                original_text=original,
                detection_source=span.get("source"),
                document_id=document_id,
            )
            placeholder_to_marker[job_placeholder] = resolved.marker
            if resolved.created:
                new_markers += 1
            else:
                reused_markers += 1
            mapping_entry_id = resolved.mapping_entry.id
        else:
            existing_marker = placeholder_to_marker[job_placeholder]
            entry = mapping_repo.find_by_marker(
                container_id, existing_marker
            )
            assert entry is not None  # we just resolved it
            mapping_entry_id = entry.id

        container_marker = placeholder_to_marker[job_placeholder]
        # Keep the original byte positions from the job — they're
        # relative to the original (pre-redaction) source. We do NOT
        # try to re-derive offsets in the new text; that's fine for
        # Sprint 5 since the audit trail still ties span → mapping_entry
        # → original_text.
        container_spans.append(
            {
                "container_document_id": document_id,
                "mapping_entry_id": mapping_entry_id,
                "entity_type": entity_type,
                "marker": container_marker,
                "original_text": original,
                "start_char": int(span.get("doc_start", 0) or 0),
                "end_char": int(span.get("doc_end", 0) or 0),
                "confidence": span.get("confidence"),
                "detection_source": span.get("source"),
                "review_status": "auto",
            }
        )

    pseudonymized_text = _normalise_for_replace(
        redacted_text, placeholder_to_marker
    )
    span_repo.add_many(container_spans)

    logger.info(
        "Promotion done container_id=%s document_id=%s "
        "spans=%d new_markers=%d reused_markers=%d",
        container_id,
        document_id,
        len(container_spans),
        new_markers,
        reused_markers,
    )
    return PromotionResult(
        pseudonymized_text=pseudonymized_text,
        span_count=len(container_spans),
        new_markers=new_markers,
        reused_markers=reused_markers,
    )


def load_applied_spans(spans_path: str | Path) -> list[dict[str, Any]]:
    """Convenience helper — read the JSON the JobService persisted."""
    return json.loads(Path(spans_path).read_text(encoding="utf-8"))
