"""Detector comparison core — OPF model versus deterministic regex.

This module powers the diagnostic "Comparação de detectores" mode. It does
**not** anonymize, approve or reject documents and never mutates a job's
main status. Its only job is to overlay spans produced by the OPF model
against spans produced by the regex-only client, classify their relation
per block, and aggregate the result for inspection.

Privacy
-------

* No raw text, span text, hashes, replacements or previews are ever
  passed to ``logger``. The dataclasses optionally carry ``text_preview``
  / ``context_preview`` strings so the UI can show small windows of
  context, but the values are kept short and never written to logs.
* All log entries are metadata only (counts, block ids, ratios).
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Literal

from .models import DetectedSpan

logger = logging.getLogger(__name__)


# Ratios computed using the Jaccard index — intersection over union, in [0,1].
PARTIAL_OVERLAP_THRESHOLD: float = 0.30
STRONG_OVERLAP_THRESHOLD: float = 0.90

# Length of the small preview window stored on the report objects (chars).
_TEXT_PREVIEW_MAX: int = 60
_CONTEXT_WINDOW: int = 24
_PREVIEW_PLACEHOLDER: str = "‹…›"


ComparisonStatus = Literal[
    "both",
    "opf_only",
    "regex_only",
    "partial_overlap",
    "type_conflict",
]

_ALL_STATUSES: tuple[ComparisonStatus, ...] = (
    "both",
    "opf_only",
    "regex_only",
    "partial_overlap",
    "type_conflict",
)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DetectorSpanView:
    """Read-only view of a span as it appears in the comparison report."""

    start: int
    end: int
    entity_type: str
    confidence: float | None = None
    source: str | None = None
    # Small preview of the raw span text, truncated. Optional — populated
    # only when the comparison is built with the full text in hand. Never
    # logged.
    text_preview: str | None = None


@dataclass(frozen=True)
class ComparisonItem:
    """A single comparison event between OPF and regex detections."""

    block_id: str
    status: ComparisonStatus
    opf_span: DetectorSpanView | None
    regex_span: DetectorSpanView | None
    overlap_ratio: float
    # Optional surrounding-context preview with the span itself elided.
    # Never logged.
    context_preview: str | None = None


@dataclass(frozen=True)
class ComparisonSummary:
    """Aggregate counts across a set of comparison items."""

    total: int = 0
    both: int = 0
    opf_only: int = 0
    regex_only: int = 0
    partial_overlap: int = 0
    type_conflict: int = 0


@dataclass(frozen=True)
class EntityTypeComparison:
    """Per-entity-type aggregate."""

    entity_type: str
    summary: ComparisonSummary


@dataclass(frozen=True)
class ComparisonBlock:
    """Raw block text passed through alongside the comparison.

    Carried on the report so the UI can render the source text with
    coloured highlights at the exact ``start``/``end`` offsets that each
    ``ComparisonItem`` already references. Persisted in the artefact
    directory only — never written to logs.
    """

    block_id: str
    text: str


@dataclass(frozen=True)
class DetectorComparisonReport:
    """Top-level report produced by :func:`build_comparison_report`."""

    job_id: str
    summary: ComparisonSummary
    by_entity_type: list[EntityTypeComparison] = field(default_factory=list)
    items: list[ComparisonItem] = field(default_factory=list)
    blocks: list[ComparisonBlock] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _overlap_ratio(a: DetectedSpan, b: DetectedSpan) -> float:
    """Jaccard overlap between two spans in [0.0, 1.0]."""
    inter_start = max(a.start, b.start)
    inter_end = min(a.end, b.end)
    intersection = inter_end - inter_start
    if intersection <= 0:
        return 0.0
    union = max(a.end, b.end) - min(a.start, b.start)
    if union <= 0:
        return 0.0
    return intersection / union


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)] + "…"


def _span_text_preview(text: str | None, span: DetectedSpan) -> str | None:
    if text is None:
        return None
    start = max(0, span.start)
    end = min(len(text), span.end)
    if start >= end:
        return ""
    return _truncate(text[start:end], _TEXT_PREVIEW_MAX)


def _context_preview(
    text: str | None,
    *,
    start: int,
    end: int,
) -> str | None:
    """Return a small surrounding window with the span itself elided."""
    if text is None:
        return None
    text_len = len(text)
    if not text_len:
        return ""
    s = max(0, min(text_len, start))
    e = max(s, min(text_len, end))
    left_start = max(0, s - _CONTEXT_WINDOW)
    right_end = min(text_len, e + _CONTEXT_WINDOW)
    left = text[left_start:s]
    right = text[e:right_end]
    return f"{left}{_PREVIEW_PLACEHOLDER}{right}"


def _to_view(span: DetectedSpan, text: str | None) -> DetectorSpanView:
    return DetectorSpanView(
        start=span.start,
        end=span.end,
        entity_type=span.entity_type,
        confidence=span.confidence,
        source=span.source,
        text_preview=_span_text_preview(text, span),
    )


def _start_of(item: ComparisonItem) -> int:
    if item.opf_span is not None and item.regex_span is not None:
        return min(item.opf_span.start, item.regex_span.start)
    if item.opf_span is not None:
        return item.opf_span.start
    if item.regex_span is not None:
        return item.regex_span.start
    return 0


def _representative_entity(item: ComparisonItem) -> str | None:
    """Pick a single entity_type to bucket an item under in the per-type
    breakdown. Regex spans win when both are present because the
    deterministic detectors carry the more specific label
    (e.g. ``cpf`` vs the model's generic ``account_number``)."""
    if item.regex_span is not None:
        return item.regex_span.entity_type
    if item.opf_span is not None:
        return item.opf_span.entity_type
    return None


# ---------------------------------------------------------------------------
# Core comparison
# ---------------------------------------------------------------------------

def compare_spans(
    opf_spans: list[DetectedSpan],
    regex_spans: list[DetectedSpan],
    block_id: str,
    text: str | None = None,
) -> list[ComparisonItem]:
    """Pair OPF and regex spans within the same block and classify each
    relation.

    The pairing is greedy by descending Jaccard overlap: highest-ratio
    pair first, then the next available, and so on. A regex span is
    consumed by at most one OPF span — once paired, it cannot be picked
    up by another OPF span.

    * ``overlap_ratio >= STRONG_OVERLAP_THRESHOLD`` → ``both`` if entity
      types match, ``type_conflict`` otherwise.
    * ``PARTIAL_OVERLAP_THRESHOLD <= overlap_ratio < STRONG_OVERLAP_THRESHOLD``
      → ``partial_overlap``.
    * Unpaired OPF spans → ``opf_only``.
    * Unpaired regex spans → ``regex_only``.

    The ``text`` argument is optional. When provided, the produced views
    carry small previews used by the UI; the previews are never logged.
    """
    candidates: list[tuple[float, int, int]] = []
    for i, o in enumerate(opf_spans):
        for j, r in enumerate(regex_spans):
            ratio = _overlap_ratio(o, r)
            if ratio >= PARTIAL_OVERLAP_THRESHOLD:
                candidates.append((ratio, i, j))
    # Highest overlap first; tie-break by indices so the order is stable.
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))

    consumed_opf: set[int] = set()
    consumed_regex: set[int] = set()
    items: list[ComparisonItem] = []

    for ratio, i, j in candidates:
        if i in consumed_opf or j in consumed_regex:
            continue
        o = opf_spans[i]
        r = regex_spans[j]
        if ratio >= STRONG_OVERLAP_THRESHOLD:
            status: ComparisonStatus = (
                "both" if o.entity_type == r.entity_type else "type_conflict"
            )
        else:
            status = "partial_overlap"
        items.append(
            ComparisonItem(
                block_id=block_id,
                status=status,
                opf_span=_to_view(o, text),
                regex_span=_to_view(r, text),
                overlap_ratio=ratio,
                context_preview=_context_preview(
                    text,
                    start=min(o.start, r.start),
                    end=max(o.end, r.end),
                ),
            )
        )
        consumed_opf.add(i)
        consumed_regex.add(j)

    for i, o in enumerate(opf_spans):
        if i in consumed_opf:
            continue
        items.append(
            ComparisonItem(
                block_id=block_id,
                status="opf_only",
                opf_span=_to_view(o, text),
                regex_span=None,
                overlap_ratio=0.0,
                context_preview=_context_preview(text, start=o.start, end=o.end),
            )
        )

    for j, r in enumerate(regex_spans):
        if j in consumed_regex:
            continue
        items.append(
            ComparisonItem(
                block_id=block_id,
                status="regex_only",
                opf_span=None,
                regex_span=_to_view(r, text),
                overlap_ratio=0.0,
                context_preview=_context_preview(text, start=r.start, end=r.end),
            )
        )

    items.sort(key=lambda it: (_start_of(it), it.status))

    logger.debug(
        "compare_spans block_id=%s opf=%d regex=%d items=%d "
        "both=%d opf_only=%d regex_only=%d partial=%d conflict=%d",
        block_id,
        len(opf_spans),
        len(regex_spans),
        len(items),
        sum(1 for i in items if i.status == "both"),
        sum(1 for i in items if i.status == "opf_only"),
        sum(1 for i in items if i.status == "regex_only"),
        sum(1 for i in items if i.status == "partial_overlap"),
        sum(1 for i in items if i.status == "type_conflict"),
    )
    return items


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _summarize(items: Iterable[ComparisonItem]) -> ComparisonSummary:
    counts: Counter[ComparisonStatus] = Counter()
    total = 0
    for item in items:
        total += 1
        counts[item.status] += 1
    return ComparisonSummary(
        total=total,
        both=counts.get("both", 0),
        opf_only=counts.get("opf_only", 0),
        regex_only=counts.get("regex_only", 0),
        partial_overlap=counts.get("partial_overlap", 0),
        type_conflict=counts.get("type_conflict", 0),
    )


def build_comparison_report(
    job_id: str,
    block_results: list[ComparisonItem],
    blocks: list[ComparisonBlock] | None = None,
) -> DetectorComparisonReport:
    """Aggregate per-block ``ComparisonItem`` lists into a full report.

    ``block_results`` is a flat list of comparison items (each carrying
    its own ``block_id``) — typically the concatenation of every per-block
    output of :func:`compare_spans`.

    ``blocks`` is the (optional) list of raw block texts that the items
    reference. When provided, the UI can render the source text with
    coloured highlights at the items' offsets. When omitted, the report
    is still complete and the UI falls back to the items table only.
    """
    summary = _summarize(block_results)

    by_type: dict[str, list[ComparisonItem]] = {}
    for item in block_results:
        entity = _representative_entity(item)
        if entity is None:
            continue
        by_type.setdefault(entity, []).append(item)

    by_entity_type = [
        EntityTypeComparison(entity_type=et, summary=_summarize(its))
        for et, its in sorted(by_type.items())
    ]

    sorted_items = sorted(
        block_results,
        key=lambda it: (it.block_id, _start_of(it), it.status),
    )

    logger.info(
        "DetectorComparisonReport built job_id=%s items=%d "
        "both=%d opf_only=%d regex_only=%d partial=%d conflict=%d types=%d",
        job_id,
        summary.total,
        summary.both,
        summary.opf_only,
        summary.regex_only,
        summary.partial_overlap,
        summary.type_conflict,
        len(by_entity_type),
    )

    return DetectorComparisonReport(
        job_id=job_id,
        summary=summary,
        by_entity_type=by_entity_type,
        items=sorted_items,
        blocks=list(blocks or []),
    )


__all__ = [
    "ComparisonBlock",
    "ComparisonItem",
    "ComparisonStatus",
    "ComparisonSummary",
    "DetectorComparisonReport",
    "DetectorSpanView",
    "EntityTypeComparison",
    "PARTIAL_OVERLAP_THRESHOLD",
    "STRONG_OVERLAP_THRESHOLD",
    "build_comparison_report",
    "compare_spans",
]
