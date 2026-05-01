from __future__ import annotations

import hashlib
import logging
from collections import Counter

from .models import AppliedSpan, DetectedSpan, RedactionResult, Strategy
from .policy import Policy

logger = logging.getLogger(__name__)

# Stable pseudonym pool — index chosen by hash so it is deterministic per value.
_PSEUDONYM_NAMES = [
    "Alex Jordan", "Blake Morgan", "Casey Rivera", "Dana Quinn",
    "Elliot Shaw", "Finley Park", "Gray Wren", "Harper Lane",
]


def _pseudonym_for(original: str, entity_type: str) -> str:
    digest = int(hashlib.sha256(f"{entity_type}:{original}".encode()).hexdigest(), 16)
    return _PSEUDONYM_NAMES[digest % len(_PSEUDONYM_NAMES)]


def _resolve_spans(spans: list[DetectedSpan]) -> list[DetectedSpan]:
    """Remove overlapping spans, keeping the one with higher confidence (then wider)."""
    sorted_spans = sorted(spans, key=lambda s: (s.start, -(s.end - s.start)))
    resolved: list[DetectedSpan] = []
    last_end = -1
    for span in sorted_spans:
        if span.start >= last_end:
            resolved.append(span)
            last_end = span.end
    return resolved


class Redactor:
    def __init__(self, policy: Policy) -> None:
        self._policy = policy
        # Per-entity counter mapping a normalised value to an integer index.
        # Persists across redact() calls within the same instance so the same
        # value (e.g. an email appearing in 3 paragraphs) gets the same
        # placeholder. Reset by creating a new Redactor for each document.
        self._counters: dict[str, dict[str, int]] = {}

    def reset_counters(self) -> None:
        """Drop all per-entity counters. Call between independent documents."""
        self._counters = {}

    def redact(self, text: str, spans: list[DetectedSpan]) -> RedactionResult:
        resolved = _resolve_spans(spans)
        applied: list[AppliedSpan] = []
        counts: Counter[str] = Counter()

        # Build redacted text by iterating resolved spans left-to-right.
        parts: list[str] = []
        cursor = 0

        for span in resolved:
            entity_cfg = self._policy.get(span.entity_type)

            if entity_cfg is None:
                # Unknown entity type: log at warning level without exposing text.
                logger.warning(
                    "Unknown entity_type=%r at [%d:%d] — span skipped",
                    span.entity_type,
                    span.start,
                    span.end,
                )
                continue

            original_fragment = text[span.start : span.end]
            replacement = self._build_replacement(
                original_fragment, span.entity_type, entity_cfg.strategy, entity_cfg
            )

            parts.append(text[cursor : span.start])
            parts.append(replacement)
            cursor = span.end

            applied.append(
                AppliedSpan(
                    start=span.start,
                    end=span.end,
                    entity_type=span.entity_type,
                    strategy=entity_cfg.strategy,
                    replacement=replacement,
                    source=span.source,
                    confidence=span.confidence,
                )
            )
            counts[span.entity_type] += 1
            # Log only metadata — never the original fragment or replacement.
            logger.debug(
                "Redacted entity_type=%r strategy=%r span=[%d:%d]",
                span.entity_type,
                entity_cfg.strategy,
                span.start,
                span.end,
            )

        parts.append(text[cursor:])
        redacted_text = "".join(parts)

        return RedactionResult(
            redacted_text=redacted_text,
            applied_spans=applied,
            stats=dict(counts),
        )

    def _build_replacement(
        self, fragment: str, entity_type: str, strategy: Strategy, cfg
    ) -> str:
        if strategy == "replace":
            return cfg.label
        if strategy == "pseudonym":
            return _pseudonym_for(fragment, entity_type)
        if strategy == "mask":
            return cfg.mask_char * len(fragment)
        if strategy == "suppress":
            return ""
        if strategy == "indexed":
            return self._indexed_replacement(fragment, entity_type, cfg)
        # Unreachable if Strategy type is exhaustive, but kept for safety.
        return cfg.label

    def _indexed_replacement(
        self, fragment: str, entity_type: str, cfg
    ) -> str:
        """Produce ``[<LABEL>_NN]`` keeping the same NN for the same value.

        The dedup key is case-folded and whitespace-collapsed so e.g.
        "Maria Silva" / "MARIA SILVA" / "maria  silva" all share one index.
        """
        key = " ".join(fragment.lower().split())
        type_map = self._counters.setdefault(entity_type, {})
        if key not in type_map:
            type_map[key] = len(type_map) + 1
        idx = type_map[key]
        label = cfg.label
        # If the configured label already wraps in brackets (the common case),
        # insert "_NN" before the closing bracket: "[EMAIL]" -> "[EMAIL_01]".
        if label.endswith("]"):
            return f"{label[:-1]}_{idx:02d}]"
        return f"{label}_{idx:02d}"
