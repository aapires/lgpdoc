from __future__ import annotations

from pathlib import Path

from .base import BaseExtractor, UnsupportedFormatError
from ..document_models import ExtractionResult

_MAX_SNIFF_BYTES = 8192


class TxtExtractor(BaseExtractor):
    """Handles plain text (.txt) and Markdown (.md) files.

    Each double-newline-separated paragraph becomes one DocumentBlock.
    Binary content (null bytes) is rejected before any further processing.
    """

    supported_extensions = frozenset({".txt", ".md"})

    def extract(self, path: Path) -> ExtractionResult:
        raw_bytes = path.read_bytes()
        if b"\x00" in raw_bytes[:_MAX_SNIFF_BYTES]:
            raise UnsupportedFormatError(
                f"{path.name!r} contains null bytes — likely a binary file"
            )
        content = raw_bytes.decode("utf-8", errors="replace")
        paragraphs = content.split("\n\n")
        return self._build_result([(None, p) for p in paragraphs])
