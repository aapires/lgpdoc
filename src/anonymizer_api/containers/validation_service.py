"""Marker parser + validator for already-pseudonymized documents.

The pseudonymized import flow does NOT run detection on the document —
markers like ``[PESSOA_0001]`` are assumed to be in place. This module:

* parses well-formed markers and reports known / unknown / malformed
  against the container's mapping table,
* (Sprint 5+) detects **residual PII** — sensitive content that
  escaped the prior pseudonymization and is now visible in the text.
  Spans that overlap an existing marker are filtered out: ``PESSOA``
  inside ``[PESSOA_0001]`` is part of the marker, not a leak.

Privacy: the validator and residual detector never log the document
text or original values. Logged fields are counts only.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

from anonymizer.client import PrivacyFilterClient

from ..db.repositories import ContainerMappingEntryRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
#
# Well-formed marker: one or more uppercase letters / underscores, an
# underscore, two-or-more digits, all wrapped in square brackets.
# We accept ≥2 digits so legacy markers ([PESSOA_01]) don't get
# flagged as malformed — Sprint 2's resolver emits 4 by default but the
# spec mentioned 2-digit forms in older mappings.
#
# Possibly-malformed marker: anything that looks like a token surrounded
# by brackets but doesn't pass the strict regex. Used to flag
# typos / hand-edited markers during external processing.

_WELL_FORMED_RE = re.compile(r"\[[A-Z][A-Z_]*_\d{2,}\]")

# Catch-all for marker-like tokens. We restrict to short bracketed
# chunks to keep noise low (any line of regular text in brackets
# would otherwise be reported).
_MARKER_LIKE_RE = re.compile(r"\[[A-Za-z0-9_\- ]{1,40}\]")


@dataclass(frozen=True)
class ParsedMarker:
    """A literal marker token found in the text."""

    text: str
    label: str
    index: int
    start: int
    end: int


@dataclass(frozen=True)
class ValidationSummary:
    """What the validator found in a pseudonymized document.

    All three lists hold the *unique* tokens — duplicates collapse so the
    UI can render a clean count without re-deduping.
    """

    total_well_formed: int = 0
    known_markers: list[str] = field(default_factory=list)
    unknown_markers: list[str] = field(default_factory=list)
    malformed_markers: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """True iff there are no unknown or malformed markers — every
        well-formed marker resolves to a mapping entry."""
        return not self.unknown_markers and not self.malformed_markers

    def to_dict(self) -> dict[str, object]:
        return {
            "total_well_formed": self.total_well_formed,
            "known_markers": list(self.known_markers),
            "unknown_markers": list(self.unknown_markers),
            "malformed_markers": list(self.malformed_markers),
            "is_clean": self.is_clean,
        }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_markers(text: str) -> list[ParsedMarker]:
    """Return every well-formed marker found in ``text``, in document
    order. Duplicates are kept — call sites that want unique tokens
    de-duplicate themselves."""
    out: list[ParsedMarker] = []
    for m in _WELL_FORMED_RE.finditer(text):
        token = m.group()
        # Token shape guaranteed by the regex; safe to split.
        inner = token[1:-1]
        underscore = inner.rindex("_")
        label = inner[:underscore]
        idx_str = inner[underscore + 1 :]
        out.append(
            ParsedMarker(
                text=token,
                label=label,
                index=int(idx_str),
                start=m.start(),
                end=m.end(),
            )
        )
    return out


def find_malformed_marker_candidates(text: str) -> list[str]:
    """Return candidate marker-like tokens that fail the strict regex.

    Examples that come back in this list:
    * ``[joao_silva]`` (lowercase),
    * ``[PESSOA_]`` (missing digits),
    * ``[CPF_ABC]`` (non-numeric index),
    * ``[123_FOO]`` (leading digits in label).
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in _MARKER_LIKE_RE.finditer(text):
        token = m.group()
        if _WELL_FORMED_RE.fullmatch(token):
            continue  # well-formed — not a candidate
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


# ---------------------------------------------------------------------------
# Validation against a container's mapping
# ---------------------------------------------------------------------------

def validate_pseudonymized_text(
    *,
    container_id: str,
    text: str,
    repo: ContainerMappingEntryRepository,
) -> ValidationSummary:
    """Compare every well-formed marker in ``text`` against the
    container's mapping table. Buckets:

    * ``known_markers`` — well-formed AND present in the mapping
    * ``unknown_markers`` — well-formed but NOT in the mapping
    * ``malformed_markers`` — bracketed tokens that fail the strict regex

    The container_id filter is the only safe scope for a marker lookup;
    the same marker text in a different container would point at a
    different real value.
    """
    parsed = parse_markers(text)
    unique_tokens: list[str] = []
    seen: set[str] = set()
    for p in parsed:
        if p.text in seen:
            continue
        seen.add(p.text)
        unique_tokens.append(p.text)

    known: list[str] = []
    unknown: list[str] = []
    for token in unique_tokens:
        entry = repo.find_by_marker(container_id, token)
        if entry is not None:
            known.append(token)
        else:
            unknown.append(token)

    malformed = find_malformed_marker_candidates(text)

    summary = ValidationSummary(
        total_well_formed=len(parsed),
        known_markers=known,
        unknown_markers=unknown,
        malformed_markers=malformed,
    )
    logger.info(
        "Pseudonymized validation done container_id=%s "
        "total=%d unique=%d known=%d unknown=%d malformed=%d",
        container_id,
        summary.total_well_formed,
        len(unique_tokens),
        len(known),
        len(unknown),
        len(malformed),
    )
    return summary


# ---------------------------------------------------------------------------
# Residual PII detection — find sensitive content NOT yet wrapped in a marker
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResidualPiiSpan:
    """A span of suspected PII the augmented detector found in the
    pseudonymized text — outside any marker. The reviewer either marks
    it as a true positive (and it gets anonymised manually, allocating
    a new container marker) or ignores it as a false positive."""

    start: int
    end: int
    entity_type: str
    confidence: float | None
    detection_source: str | None
    fragment: str  # the literal text the detector flagged
    fragment_hash: str  # SHA-256 — useful to dedupe in the UI


def _hash_fragment(text: str) -> str:
    normalised = " ".join(text.lower().split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def detect_residual_pii(
    *, text: str, client: PrivacyFilterClient
) -> list[ResidualPiiSpan]:
    """Run the augmented detector over an already-pseudonymized text
    and return only the spans that DON'T overlap any well-formed
    marker — those are the suspected leaks.

    The augmented client is the same one the production pipeline uses
    (OPF + case normalisation + BR regex augmentations). We are NOT
    using ``RegexOnlyClient`` here — that's reserved for the diagnostic
    comparison mode and would miss model-only detections.
    """
    detected = client.detect(text)
    markers = parse_markers(text)
    marker_ranges: list[tuple[int, int]] = [(m.start, m.end) for m in markers]

    residual: list[ResidualPiiSpan] = []
    for span in detected:
        # Drop anything that overlaps a marker — those are part of the
        # ``[PESSOA_0001]`` token, not a real PII leak.
        if any(
            span.start < me and span.end > ms
            for ms, me in marker_ranges
        ):
            continue
        # Drop empty/zero-length defensively.
        if span.end <= span.start:
            continue
        fragment = text[span.start : span.end]
        if not fragment.strip():
            continue
        residual.append(
            ResidualPiiSpan(
                start=span.start,
                end=span.end,
                entity_type=span.entity_type,
                confidence=span.confidence,
                detection_source=span.source,
                fragment=fragment,
                fragment_hash=_hash_fragment(fragment),
            )
        )

    logger.info(
        "Residual PII scan done detected=%d markers=%d residual=%d",
        len(detected),
        len(markers),
        len(residual),
    )
    return residual
