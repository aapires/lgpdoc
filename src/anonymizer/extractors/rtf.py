"""Rich Text Format (.rtf) extractor.

RTF is a tagged plain-text format. The ``striprtf`` library converts
it to plain text by removing the formatting markers. This extractor
emits each non-empty paragraph (split on blank lines) as one
DocumentBlock — same shape as the .txt / .md extractor.

Tables embedded in RTF documents come out as tab-separated text
rather than Markdown — RTF table reconstruction is significantly
more involved than DOCX/XLSX (which expose structured cell APIs)
and out of scope for this version. If you need structured tables,
prefer .docx or .xlsx as input formats.
"""
from __future__ import annotations

from pathlib import Path

from .base import BaseExtractor, UnsupportedFormatError
from ..document_models import ExtractionResult


class RtfExtractor(BaseExtractor):
    """Extracts plain text from RTF files via ``striprtf``."""

    supported_extensions = frozenset({".rtf"})

    def extract(self, path: Path) -> ExtractionResult:
        try:
            from striprtf.striprtf import rtf_to_text
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "striprtf is required for RTF extraction. "
                "Install it with: pip install 'striprtf>=0.0.26'"
            ) from exc

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise UnsupportedFormatError(
                f"{path.name!r} could not be read: {exc}"
            ) from exc

        # ``striprtf`` returns a single string with newlines preserved.
        try:
            text = rtf_to_text(content, errors="ignore")
        except Exception as exc:  # noqa: BLE001
            raise UnsupportedFormatError(
                f"{path.name!r} could not be parsed as RTF: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        # Split into paragraphs at blank lines — same convention as
        # the plain-text extractor.
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        raw: list[tuple[int | None, str]] = [(None, p) for p in paragraphs]
        return self._build_result(raw)
