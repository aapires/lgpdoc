"""Restore — replace markers with their originals using the container's
mapping table.

The non-negotiable invariant of this module: every lookup is filtered
by ``container_id``. A marker that exists in container B but not in
container A is *unknown* in container A, regardless of how the text
looks. Cross-container leakage would defeat the whole feature, so the
public API only takes a bound ``container_id`` and a repository.

Privacy
-------

Restored text by definition contains the originals. This module never
writes that text to logs — only counts (replaced, unknown, malformed)
plus the ``container_id`` make it through.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..db.repositories import ContainerMappingEntryRepository
from .validation_service import (
    find_malformed_marker_candidates,
    parse_markers,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RestoreSummary:
    """Outcome of a restore operation.

    ``restored_text`` carries the result with every known marker
    replaced by its original. Unknown / malformed tokens are left as-is
    in the text so the caller can review them visually without losing
    context.
    """

    restored_text: str
    # Number of marker *tokens* replaced (counts repetitions).
    replaced_token_count: int = 0
    # Number of distinct marker strings that were resolved.
    replaced_unique_count: int = 0
    # Well-formed markers not present in the container's mapping —
    # left untouched in the output.
    unknown_markers: list[str] = field(default_factory=list)
    # Marker-shaped tokens that fail the strict regex.
    malformed_markers: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.unknown_markers and not self.malformed_markers

    def to_dict(self) -> dict[str, object]:
        return {
            "restored_text": self.restored_text,
            "replaced_token_count": self.replaced_token_count,
            "replaced_unique_count": self.replaced_unique_count,
            "unknown_markers": list(self.unknown_markers),
            "malformed_markers": list(self.malformed_markers),
            "is_clean": self.is_clean,
        }


def restore_text(
    *,
    container_id: str,
    text: str,
    repo: ContainerMappingEntryRepository,
) -> RestoreSummary:
    """Replace every well-formed marker in ``text`` that is registered
    in the container's mapping with its original. Unknown markers are
    left as-is and reported. Malformed candidates are also reported but
    never touched.

    The ``container_id`` filter is the only safe scope: the same marker
    text in another container points at a different real value and
    must not surface here.
    """
    parsed = parse_markers(text)
    unique_markers: list[str] = []
    seen: set[str] = set()
    for p in parsed:
        if p.text in seen:
            continue
        seen.add(p.text)
        unique_markers.append(p.text)

    replacements: dict[str, str] = {}
    unknown: list[str] = []
    for marker in unique_markers:
        entry = repo.find_by_marker(container_id, marker)
        if entry is None:
            unknown.append(marker)
            continue
        replacements[marker] = entry.original_text

    # Apply substitutions. Sort longest-first as a defensive measure;
    # the bracketed marker shape rules out genuine overlap, but keeping
    # the order stable is cheap insurance.
    restored = text
    replaced_token_count = 0
    for marker in sorted(replacements.keys(), key=len, reverse=True):
        occurrences = restored.count(marker)
        if occurrences == 0:
            continue
        restored = restored.replace(marker, replacements[marker])
        replaced_token_count += occurrences

    malformed = find_malformed_marker_candidates(text)

    summary = RestoreSummary(
        restored_text=restored,
        replaced_token_count=replaced_token_count,
        replaced_unique_count=len(replacements),
        unknown_markers=unknown,
        malformed_markers=malformed,
    )
    logger.info(
        "Restore done container_id=%s tokens=%d unique=%d "
        "unknown=%d malformed=%d",
        container_id,
        replaced_token_count,
        len(replacements),
        len(unknown),
        len(malformed),
    )
    return summary
