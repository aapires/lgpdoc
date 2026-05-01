"""Pipeline for raw sensitive documents inside a container.

The flow mirrors the production redaction pipeline (extract → detect →
redact → assemble → save) but uses the container-scoped marker resolver
instead of the document-local indexed strategy used by ``Redactor``.

Why a separate pipeline?

* The production ``Redactor`` resets index counters per document, which
  is exactly the wrong thing for containers — the whole point of the
  container is that the same person across two documents shares a
  marker. Reusing ``Redactor`` would defeat that.
* The redactor's overlap resolution is reused here in a stripped-down
  form (longest span wins on overlap, ties broken by start position).

Privacy
-------

Logs only carry container_id, document_id, block counts, span counts,
and entity types. Original text and markers tied to specific values
must not appear in log records.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from anonymizer.client import PrivacyFilterClient
from anonymizer.document_models import BLOCK_SEPARATOR
from anonymizer.models import DetectedSpan
from anonymizer.pipeline import extract_document

from .marker_resolver import MarkerResolver

logger = logging.getLogger(__name__)


@dataclass
class ContainerPipelineResult:
    pseudonymized_text: str
    spans: list[dict[str, object]]
    block_count: int
    new_markers: int
    reused_markers: int


def _resolve_overlap(spans: list[DetectedSpan]) -> list[DetectedSpan]:
    """Drop overlapping spans deterministically.

    Sort by ``(start, -length)`` so the longest span at each position
    wins; once we kept a span, anything starting before that span ends
    is dropped.
    """
    if not spans:
        return []
    sorted_spans = sorted(
        spans, key=lambda s: (s.start, -(s.end - s.start))
    )
    out: list[DetectedSpan] = []
    cursor = 0
    for span in sorted_spans:
        if span.start < cursor:
            continue
        if span.end <= span.start:
            continue
        out.append(span)
        cursor = span.end
    return out


def run_raw_document(
    *,
    source_path: Path,
    client: PrivacyFilterClient,
    resolver: MarkerResolver,
    document_id: str,
) -> ContainerPipelineResult:
    """Process a raw sensitive document through the container's
    pseudonymization pipeline.

    Parameters
    ----------
    source_path:
        Path to the quarantined upload (raw bytes on disk).
    client:
        Augmented production client (OPF + case normalisation +
        regex augmentations). Containers DO use the augmented client
        — the rule that comparison must skip ``CompositeClient`` is
        scoped to the diagnostic mode only.
    resolver:
        ``MarkerResolver`` already bound to the target container.
    document_id:
        ID of the ``ContainerDocumentModel`` row this run belongs to.
        Stamped onto every persisted span and every new mapping entry.
    """
    extraction = extract_document(source_path)
    redacted_blocks: list[str] = []
    span_rows: list[dict[str, object]] = []

    new_markers = 0
    reused_markers = 0

    # Document-level offset — the position in the assembled
    # pseudonymized text where the next block will start. Used to give
    # span rows a doc-relative ``start_char`` / ``end_char``.
    doc_cursor = 0

    for block_idx, block in enumerate(extraction.blocks):
        detected = client.detect(block.text)
        kept = _resolve_overlap(detected)

        # Walk left-to-right replacing each kept span with its container
        # marker. ``new_text`` is the pseudonymised version of the block.
        out_chunks: list[str] = []
        cursor = 0
        for span in kept:
            if span.start > cursor:
                out_chunks.append(block.text[cursor : span.start])
            original = block.text[span.start : span.end]
            resolved = resolver.resolve(
                entity_type=span.entity_type,
                original_text=original,
                detection_source=span.source,
                document_id=document_id,
            )
            if resolved.created:
                new_markers += 1
            else:
                reused_markers += 1
            out_chunks.append(resolved.marker)
            # Doc-level positions for the persisted span row — relative
            # to the assembled pseudonymised text we're emitting.
            block_offset = sum(len(c) for c in out_chunks[:-1])
            span_rows.append(
                {
                    "container_document_id": document_id,
                    "mapping_entry_id": resolved.mapping_entry.id,
                    "entity_type": span.entity_type,
                    "marker": resolved.marker,
                    "original_text": original,
                    "start_char": doc_cursor + block_offset,
                    "end_char": doc_cursor + block_offset
                    + len(resolved.marker),
                    "confidence": span.confidence,
                    "detection_source": span.source,
                    "review_status": "auto",
                }
            )
            cursor = span.end
        if cursor < len(block.text):
            out_chunks.append(block.text[cursor:])
        new_block = "".join(out_chunks)
        redacted_blocks.append(new_block)

        # Advance doc cursor by block + separator (separator only between
        # blocks, not after the last one).
        doc_cursor += len(new_block)
        if block_idx < len(extraction.blocks) - 1:
            doc_cursor += len(BLOCK_SEPARATOR)

    pseudonymized_text = BLOCK_SEPARATOR.join(redacted_blocks)

    logger.info(
        "Container pipeline done container_id=%s document_id=%s "
        "blocks=%d spans=%d new_markers=%d reused_markers=%d",
        resolver.container_id,
        document_id,
        len(extraction.blocks),
        len(span_rows),
        new_markers,
        reused_markers,
    )
    return ContainerPipelineResult(
        pseudonymized_text=pseudonymized_text,
        spans=span_rows,
        block_count=len(extraction.blocks),
        new_markers=new_markers,
        reused_markers=reused_markers,
    )
