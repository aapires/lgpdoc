from __future__ import annotations

import re
from abc import ABC, abstractmethod

from .models import DetectedSpan


class PrivacyFilterClient(ABC):
    """Internal interface for PII detection backends."""

    @abstractmethod
    def detect(self, text: str) -> list[DetectedSpan]:
        """Return detected PII spans for *text* without logging its contents."""


# ---------------------------------------------------------------------------
# Patterns used by the mock client — intentionally simple regex heuristics.
# ---------------------------------------------------------------------------
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_email", re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")),
    ("private_phone", re.compile(r"\+?[\d][\d\s\-().]{6,}\d")),
    ("account_number", re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b")),
    ("private_date", re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{2}/\d{2}/\d{4}\b")),
    # Names: two or three capitalised words (heuristic only — good enough for fixtures)
    ("private_person", re.compile(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+){1,2}\b")),
]


class MockPrivacyFilterClient(PrivacyFilterClient):
    """Regex-based mock for unit tests — no network calls, no real data."""

    def detect(self, text: str) -> list[DetectedSpan]:
        spans: list[DetectedSpan] = []
        for entity_type, pattern in _PATTERNS:
            for match in pattern.finditer(text):
                spans.append(
                    DetectedSpan(
                        start=match.start(),
                        end=match.end(),
                        entity_type=entity_type,
                        confidence=0.9,
                    )
                )
        return spans
