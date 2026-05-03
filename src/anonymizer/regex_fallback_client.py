"""Conservative ``PrivacyFilterClient`` used when the OPF toggle is OFF.

The mock client (``MockPrivacyFilterClient``) carries a heuristic
person-name regex that's useful in tests but disastrous in production:
combined with the case-normalisation wrapper, *every* ALL-CAPS word
sequence in a Brazilian document gets matched as a person and produces
86+ phantom detections in real-world docs (`unknown` source, no model
behind them). That's worse than no detection at all — it pushes
reviewers to approve nonsense and pollutes the mapping table.

This client emits only patterns that regex can decide *without context*:

* ``private_email`` — email syntax is unambiguous; deterministic match.

Names, phones, dates, IDs, etc. are handled either by the OPF model
(when the toggle is ON) or by the deterministic Brazilian augmentations
(``detect_cpfs``, ``detect_cnpjs``, ``detect_dates``, ``detect_oab``…)
that ``make_augmented_client`` adds on top of every base. None of
those false-fire on caps text.
"""
from __future__ import annotations

import re

from .client import PrivacyFilterClient
from .models import DetectedSpan


_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


class RegexFallbackClient(PrivacyFilterClient):
    """Production fallback when the OPF toggle is OFF — deterministic only."""

    def detect(self, text: str) -> list[DetectedSpan]:
        spans: list[DetectedSpan] = []
        for match in _EMAIL_RE.finditer(text):
            spans.append(
                DetectedSpan(
                    start=match.start(),
                    end=match.end(),
                    entity_type="private_email",
                    confidence=1.0,
                    source="regex_fallback",
                )
            )
        return spans
