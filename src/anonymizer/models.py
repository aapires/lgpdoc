from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Strategy = Literal["replace", "pseudonym", "mask", "suppress", "indexed"]


@dataclass(frozen=True)
class DetectedSpan:
    start: int
    end: int
    entity_type: str
    # confidence in [0.0, 1.0]; None means unknown
    confidence: float | None = None
    # SHA-256 of the raw span text — allows auditing without persisting plaintext
    text_hash: str | None = None
    # identifies which backend produced this span
    source: str | None = None


@dataclass(frozen=True)
class AppliedSpan:
    start: int
    end: int
    entity_type: str
    strategy: Strategy
    replacement: str
    # Carried over from DetectedSpan so the review UI can show which detector
    # produced this span (the model, a deterministic regex, etc.).
    source: str | None = None
    confidence: float | None = None


@dataclass
class RedactionResult:
    redacted_text: str
    applied_spans: list[AppliedSpan] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
