"""Standalone image extractor — runs OCR on .png / .jpg / .jpeg
uploads. Produces a single DocumentBlock with no page concept."""
from __future__ import annotations

import logging
from pathlib import Path

from .base import BaseExtractor, UnsupportedFormatError
from . import ocr as _ocr
from ..document_models import ExtractionResult

logger = logging.getLogger(__name__)


class ImageExtractor(BaseExtractor):
    """Extracts text from a standalone image via Tesseract OCR.

    Unlike the PdfExtractor (which uses OCR as a fallback), this
    extractor REQUIRES the ``[ocr]`` extras at extraction time —
    there's no plain-text path for an image. Without the deps the
    extraction raises ``UnsupportedFormatError`` so the upload is
    rejected cleanly with a 400.
    """

    supported_extensions = frozenset({".png", ".jpg", ".jpeg"})

    def extract(self, path: Path) -> ExtractionResult:
        if not _ocr.is_available():
            raise UnsupportedFormatError(
                "Image uploads require OCR. Install the [ocr] extras "
                "(pytesseract + pdf2image + Pillow) and the system-level "
                "tesseract binary. See docs/local_setup.md."
            )

        try:
            text = _ocr.ocr_image(path)
        except _ocr.OcrDependencyError as exc:
            raise UnsupportedFormatError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            # Tesseract binary missing on PATH, corrupt image, etc.
            raise UnsupportedFormatError(
                f"OCR failed for {path.name!r}: {type(exc).__name__}: {exc}"
            ) from exc

        # One block — no page concept for a single image.
        raw: list[tuple[int | None, str]] = [(None, text)]
        result = self._build_result(raw)
        logger.info(
            "Image OCR done file=%s blocks=%d",
            path.name,
            len(result.blocks),
        )
        return result
