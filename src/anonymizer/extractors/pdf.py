from __future__ import annotations

import logging
from pathlib import Path

from .base import BaseExtractor, UnsupportedFormatError
from ..document_models import ExtractionResult

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore[assignment,misc]

from . import ocr as _ocr

logger = logging.getLogger(__name__)


class PdfExtractor(BaseExtractor):
    """Extracts text from PDF files.

    Strategy:
    1. Try ``pypdf``'s native text extraction (the embedded text layer).
    2. For each page that yields fewer than ``ocr.DEFAULT_MIN_TEXT_CHARS``
       characters of non-whitespace text, **fall back to Tesseract OCR**
       — that page is most likely a scan or has a broken text layer.
    3. If the optional ``[ocr]`` extras aren't installed, OCR is
       silently skipped — pages without a text layer just produce
       empty blocks (the legacy behaviour).

    One DocumentBlock is produced per page that has any text after
    both passes.
    """

    supported_extensions = frozenset({".pdf"})

    def extract(self, path: Path) -> ExtractionResult:
        if PdfReader is None:  # pragma: no cover
            raise RuntimeError(
                "pypdf is required for PDF extraction. "
                "Install it with: pip install 'pypdf>=4.0'"
            )

        try:
            reader = PdfReader(str(path))
        except Exception as exc:
            raise UnsupportedFormatError(
                f"{path.name!r} could not be parsed as a PDF: {exc}"
            ) from exc

        # Pass 1 — native text layer extraction
        per_page: dict[int, str] = {}
        ocr_candidates: list[int] = []
        for i, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001
                text = ""
            per_page[i] = text
            if _ocr.needs_ocr(text):
                ocr_candidates.append(i)

        # Pass 2 — OCR fallback for pages that look scanned
        if ocr_candidates:
            if _ocr.is_available():
                logger.info(
                    "PDF OCR fallback file=%s pages=%d",
                    path.name,
                    len(ocr_candidates),
                )
                try:
                    ocr_text = _ocr.ocr_pdf_pages(path, ocr_candidates)
                except _ocr.OcrDependencyError as exc:
                    # The deps disappeared between is_available() and
                    # the actual call (very unlikely, defensive).
                    logger.warning(
                        "OCR aborted file=%s reason=%s",
                        path.name,
                        exc.__class__.__name__,
                    )
                    ocr_text = {}
                except Exception as exc:  # noqa: BLE001 — record + continue
                    # Common case: tesseract binary missing on PATH.
                    # Don't crash extraction — log and continue with
                    # whatever the text layer yielded.
                    logger.warning(
                        "OCR run failed file=%s reason=%s: %s",
                        path.name,
                        type(exc).__name__,
                        exc,
                    )
                    ocr_text = {}

                for page_num, text in ocr_text.items():
                    # Only overwrite when OCR actually produced more text
                    # than the native pass. Catches cases where pypdf
                    # extracted a header but the body is a scan.
                    if len(text.strip()) > len(per_page.get(page_num, "").strip()):
                        per_page[page_num] = text
            else:
                logger.info(
                    "PDF OCR skipped (deps missing) file=%s pages=%d",
                    path.name,
                    len(ocr_candidates),
                )

        raw: list[tuple[int | None, str]] = [
            (i, per_page.get(i, "")) for i in sorted(per_page.keys())
        ]
        return self._build_result(raw)
