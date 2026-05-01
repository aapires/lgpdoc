"""Optical Character Recognition helpers for scanned documents.

Two entry points:

* :func:`ocr_pdf_pages` — given a PDF path and a list of 1-based page
  numbers, render those pages to images via ``pdf2image`` and run
  Tesseract on each. Used by :class:`PdfExtractor` as a fallback when
  the text layer is empty.
* :func:`ocr_image` — run Tesseract directly on an image file
  (``.png``, ``.jpg``, ``.jpeg``).

Both lazily import ``pytesseract`` / ``pdf2image`` / ``PIL`` so the
core package keeps working when the optional ``[ocr]`` extras aren't
installed. A clear ``RuntimeError`` is raised at first call when the
deps are missing — never at module import time.

Privacy
-------

OCR output is document content. No log line in this module includes
the recognised text. Only counts (page numbers, char counts) make it
through.

The Tesseract language pack defaults to Brazilian Portuguese
(``por``). Override with the ``ANONYMIZER_OCR_LANGUAGE`` env var.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


# Default language. Tesseract uses ISO 639-2 codes; ``por`` covers
# Brazilian Portuguese (the model is monolithic for the language).
# Override per-process via env var without code changes.
DEFAULT_LANGUAGE: str = os.environ.get("ANONYMIZER_OCR_LANGUAGE", "por")

# Pages whose text layer yields fewer than this many non-whitespace
# characters are considered "scanned" and re-extracted via OCR.
DEFAULT_MIN_TEXT_CHARS: int = 30


# ---------------------------------------------------------------------------
# Lazy import helpers
# ---------------------------------------------------------------------------

class OcrDependencyError(RuntimeError):
    """Raised when an OCR call is made without the optional deps
    installed. The message points at the install command."""


_INSTALL_HINT = (
    "OCR requires the [ocr] extras (pytesseract + pdf2image + Pillow) "
    "AND the system-level Tesseract + Poppler binaries.\n"
    "  pip install -e '.[ocr]'\n"
    "  # macOS:    brew install tesseract tesseract-lang poppler\n"
    "  # Ubuntu:   apt install tesseract-ocr tesseract-ocr-por poppler-utils"
)


def _import_pytesseract():
    try:
        import pytesseract  # type: ignore[import-not-found]

        return pytesseract
    except ImportError as exc:
        raise OcrDependencyError(_INSTALL_HINT) from exc


def _import_pdf2image():
    try:
        from pdf2image import convert_from_path  # type: ignore[import-not-found]

        return convert_from_path
    except ImportError as exc:
        raise OcrDependencyError(_INSTALL_HINT) from exc


def _import_pil():
    try:
        from PIL import Image  # type: ignore[import-not-found]

        return Image
    except ImportError as exc:
        raise OcrDependencyError(_INSTALL_HINT) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """Quick cheap check: are the Python deps importable?

    Note: this does NOT verify that the system-level ``tesseract``
    binary is on PATH. That's checked the first time we call into
    pytesseract (which raises ``TesseractNotFoundError``).
    """
    try:
        _import_pytesseract()
        _import_pdf2image()
        _import_pil()
    except OcrDependencyError:
        return False
    return True


def ocr_image(
    path: Path,
    *,
    language: str | None = None,
) -> str:
    """Run Tesseract on an image file. Returns the recognised text.

    Returns an empty string when Tesseract finds nothing — never
    raises for "no text" (that's a valid outcome). Raises
    :class:`OcrDependencyError` if the optional deps are missing.
    """
    pytesseract = _import_pytesseract()
    PIL_Image = _import_pil()

    lang = language or DEFAULT_LANGUAGE
    with PIL_Image.open(path) as img:
        text = pytesseract.image_to_string(img, lang=lang) or ""

    logger.info(
        "OCR image done path_size=%d lang=%s chars=%d",
        path.stat().st_size if path.exists() else 0,
        lang,
        len(text),
    )
    return text


def ocr_pdf_pages(
    path: Path,
    page_numbers: list[int],
    *,
    language: str | None = None,
    dpi: int = 200,
) -> dict[int, str]:
    """Run OCR on the given 1-based page numbers of a PDF.

    Returns a dict ``{page_number: text}``. Pages that fail OCR
    (Tesseract error, rendering error) map to empty strings — the
    caller decides whether that's acceptable. The function logs the
    failure with metadata only.
    """
    if not page_numbers:
        return {}

    pytesseract = _import_pytesseract()
    convert_from_path = _import_pdf2image()

    lang = language or DEFAULT_LANGUAGE
    out: dict[int, str] = {}

    # Render only the pages we need. ``pdf2image`` accepts first/last
    # page bounds — render contiguous ranges to keep the call count low.
    sorted_pages = sorted(set(page_numbers))
    for first, last in _contiguous_ranges(sorted_pages):
        try:
            images = convert_from_path(
                str(path), dpi=dpi, first_page=first, last_page=last
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PDF render failed pages=%s reason=%s",
                f"{first}-{last}",
                type(exc).__name__,
            )
            for p in range(first, last + 1):
                out[p] = ""
            continue

        for offset, img in enumerate(images):
            page_num = first + offset
            try:
                text = pytesseract.image_to_string(img, lang=lang) or ""
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "OCR page failed page=%d reason=%s",
                    page_num,
                    type(exc).__name__,
                )
                text = ""
            out[page_num] = text
            logger.info(
                "OCR page done page=%d lang=%s chars=%d",
                page_num,
                lang,
                len(text),
            )

    return out


def _contiguous_ranges(pages: list[int]) -> list[tuple[int, int]]:
    """``[1, 2, 3, 7, 8]`` → ``[(1, 3), (7, 8)]``."""
    if not pages:
        return []
    ranges: list[tuple[int, int]] = []
    start = prev = pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        ranges.append((start, prev))
        start = prev = p
    ranges.append((start, prev))
    return ranges


def needs_ocr(text: str, threshold: int = DEFAULT_MIN_TEXT_CHARS) -> bool:
    """True when ``text`` is short enough to suggest the page is a
    scan (or has a broken text layer). Defensive default at 30 chars
    catches almost-empty pages without firing on legitimately sparse
    pages (e.g. cover pages with just a title)."""
    return len(text.strip()) < threshold
