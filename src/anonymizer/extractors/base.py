from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..document_models import BLOCK_SEPARATOR, DocumentBlock, ExtractionResult


class UnsupportedFormatError(ValueError):
    """Raised when a file format is not supported or a binary file is detected."""


class BaseExtractor(ABC):
    """Contract every format extractor must satisfy."""

    supported_extensions: frozenset[str]

    @abstractmethod
    def extract(self, path: Path) -> ExtractionResult:
        """Parse *path* and return its text as a list of DocumentBlocks.

        Implementations must never log file content.
        Raise UnsupportedFormatError for structurally invalid files.
        """

    # ------------------------------------------------------------------
    # Shared helper: build ExtractionResult from (page, text) pairs.
    # ------------------------------------------------------------------

    @staticmethod
    def _build_result(raw: list[tuple[int | None, str]]) -> ExtractionResult:
        """Convert (page, text) pairs into DocumentBlocks with correct offsets."""
        blocks: list[DocumentBlock] = []
        offset = 0
        seq = 0
        for page, text in raw:
            stripped = text.strip()
            if not stripped:
                continue
            block = DocumentBlock(
                block_id=f"block-{seq:04d}",
                page=page,
                text=stripped,
                start_offset=offset,
                end_offset=offset + len(stripped),
            )
            blocks.append(block)
            offset += len(stripped) + len(BLOCK_SEPARATOR)
            seq += 1

        full_text = BLOCK_SEPARATOR.join(b.text for b in blocks)
        return ExtractionResult(blocks=blocks, full_text=full_text)
