"""Tests for the optional OCR layer.

We never invoke the real Tesseract binary in tests — that would slow
the suite down and require system deps. Instead we monkeypatch
``pytesseract.image_to_string`` to return synthetic strings, and we
assert the surrounding logic (when does OCR fire, how is its output
threaded into the pipeline, etc.) behaves correctly.

Tests are skipped gracefully when ``pytesseract`` / ``pdf2image`` /
``Pillow`` aren't installed in the dev env. CI without the extras
just won't exercise these paths.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from anonymizer.extractors import ocr as ocr_mod
from anonymizer.extractors.image import ImageExtractor
from anonymizer.extractors.base import UnsupportedFormatError


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestNeedsOcr:
    def test_short_text_triggers_ocr(self) -> None:
        assert ocr_mod.needs_ocr("") is True
        assert ocr_mod.needs_ocr("   \n   ") is True
        assert ocr_mod.needs_ocr("abc") is True  # 3 chars

    def test_long_text_skips_ocr(self) -> None:
        assert ocr_mod.needs_ocr("a" * 100) is False
        assert ocr_mod.needs_ocr("Lorem ipsum dolor sit amet, consectetur") is False

    def test_threshold_override(self) -> None:
        assert ocr_mod.needs_ocr("abcdef", threshold=4) is False
        assert ocr_mod.needs_ocr("abc", threshold=4) is True


class TestContiguousRanges:
    def test_groups_consecutive_pages(self) -> None:
        assert ocr_mod._contiguous_ranges([1, 2, 3, 7, 8, 12]) == [
            (1, 3),
            (7, 8),
            (12, 12),
        ]

    def test_empty_input(self) -> None:
        assert ocr_mod._contiguous_ranges([]) == []

    def test_single_page(self) -> None:
        assert ocr_mod._contiguous_ranges([5]) == [(5, 5)]

    def test_dedupes_via_caller(self) -> None:
        # The function itself doesn't dedupe — the public API
        # (``ocr_pdf_pages``) does that with a sorted set. Here we
        # just verify the basic contract on already-deduped input.
        assert ocr_mod._contiguous_ranges([1, 2, 4, 5]) == [(1, 2), (4, 5)]


# ---------------------------------------------------------------------------
# Dependency error path
# ---------------------------------------------------------------------------

class TestOcrDependencyMessage:
    def test_install_hint_mentions_brew_and_apt(self) -> None:
        msg = ocr_mod._INSTALL_HINT
        assert "brew install" in msg
        assert "apt install" in msg
        assert "[ocr]" in msg

    def test_image_extractor_fails_gracefully_without_deps(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The ImageExtractor must raise ``UnsupportedFormatError``
        (which the API turns into a 400) when OCR deps are missing.
        We simulate the missing-deps state by monkeypatching
        ``is_available`` to return False."""
        monkeypatch.setattr(ocr_mod, "is_available", lambda: False)
        # Create a dummy file — extraction should bail before reading it.
        path = tmp_path / "scan.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG magic, enough for the test
        with pytest.raises(UnsupportedFormatError) as exc_info:
            ImageExtractor().extract(path)
        assert "OCR" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Mocked OCR runs — gated per test so the pure helpers above always run
# ---------------------------------------------------------------------------

@pytest.fixture()
def pytesseract_mod():
    return pytest.importorskip(
        "pytesseract",
        reason="OCR extras not installed (pip install -e '.[ocr]')",
    )


@pytest.fixture()
def pil_image():
    return pytest.importorskip("PIL.Image", reason="Pillow not installed")


@pytest.fixture()
def synthetic_png(tmp_path: Path, pil_image) -> Path:
    """A 1x1 white PNG — content doesn't matter, we mock the OCR call."""
    img = pil_image.new("RGB", (1, 1), color="white")
    p = tmp_path / "scan.png"
    img.save(p, format="PNG")
    return p


class TestImageExtractorWithMockedOcr:
    def test_extract_returns_one_block_with_recognised_text(
        self,
        synthetic_png: Path,
        pytesseract_mod,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recognised = "OCR sintético: Cliente Joao Silva."
        monkeypatch.setattr(
            pytesseract_mod,
            "image_to_string",
            lambda img, lang=None: recognised,
        )
        result = ImageExtractor().extract(synthetic_png)
        assert len(result.blocks) == 1
        assert result.blocks[0].text == recognised
        assert result.blocks[0].page is None
        assert result.blocks[0].block_id == "block-0000"

    def test_empty_recognition_yields_no_blocks(
        self,
        synthetic_png: Path,
        pytesseract_mod,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            pytesseract_mod, "image_to_string", lambda img, lang=None: ""
        )
        result = ImageExtractor().extract(synthetic_png)
        # ``_build_result`` drops empty-after-strip blocks.
        assert result.blocks == []
        assert result.full_text == ""

    def test_tesseract_binary_missing_is_unsupported(
        self,
        synthetic_png: Path,
        pytesseract_mod,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If pytesseract is installed but the binary isn't, we want
        a clean 4xx — never a 500."""

        def boom(*args: Any, **kwargs: Any) -> None:
            raise pytesseract_mod.TesseractNotFoundError()

        monkeypatch.setattr(pytesseract_mod, "image_to_string", boom)
        with pytest.raises(UnsupportedFormatError) as exc_info:
            ImageExtractor().extract(synthetic_png)
        assert "tesseract" in str(exc_info.value).lower() or "OCR" in str(
            exc_info.value
        )


class TestPdfExtractorOcrFallback:
    """We mock ``PdfReader`` directly (same pattern as
    ``test_extractors.py``) instead of building a real PDF — keeps the
    test focused on the OCR-fallback logic, not on PDF generation."""

    def _make_mock_reader(self, page_texts: list[str]):
        from unittest.mock import MagicMock

        pages = []
        for text in page_texts:
            page = MagicMock()
            page.extract_text.return_value = text
            pages.append(page)
        reader = MagicMock()
        reader.pages = pages
        return reader

    def test_ocr_fallback_replaces_short_pages(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pages whose pypdf-extracted text is below the OCR threshold
        get re-extracted via OCR; the recognised text replaces the
        empty native output in the final result."""
        from unittest.mock import patch
        from anonymizer.extractors.pdf import PdfExtractor

        pdf_path = tmp_path / "mixed.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        # Page 1 — long text (skip OCR). Page 2 — empty (OCR). Page 3 — long.
        mock_reader = self._make_mock_reader(
            [
                "Página 1 com bastante texto extraível pelo pypdf.",
                "",
                "Página 3 também tem texto suficiente aqui.",
            ]
        )

        def fake_ocr(
            path: Path, page_numbers: list[int], **kwargs: Any
        ) -> dict[int, str]:
            return {p: f"OCR sintético página {p}." for p in page_numbers}

        monkeypatch.setattr(ocr_mod, "is_available", lambda: True)
        monkeypatch.setattr(ocr_mod, "ocr_pdf_pages", fake_ocr)

        with patch(
            "anonymizer.extractors.pdf.PdfReader", return_value=mock_reader
        ):
            result = PdfExtractor().extract(pdf_path)

        assert len(result.blocks) == 3
        # Page 1 + Page 3 keep native output; Page 2 carries OCR.
        assert "Página 1" in result.blocks[0].text
        assert "OCR sintético página 2" in result.blocks[1].text
        assert "Página 3" in result.blocks[2].text

    def test_ocr_silent_skip_when_deps_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without OCR extras, blank pages just yield empty blocks
        (legacy behaviour). ``ocr_pdf_pages`` must NOT be called."""
        from unittest.mock import patch
        from anonymizer.extractors.pdf import PdfExtractor

        pdf_path = tmp_path / "with_blank.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        mock_reader = self._make_mock_reader(
            [
                "Página com bastante texto extraível pelo pypdf.",
                "",  # blank → would trigger OCR if available
            ]
        )

        called = {"flag": False}

        def fail_ocr(*a: Any, **k: Any) -> dict[int, str]:
            called["flag"] = True
            return {}

        monkeypatch.setattr(ocr_mod, "is_available", lambda: False)
        monkeypatch.setattr(ocr_mod, "ocr_pdf_pages", fail_ocr)

        with patch(
            "anonymizer.extractors.pdf.PdfReader", return_value=mock_reader
        ):
            result = PdfExtractor().extract(pdf_path)

        # Just one block — the empty page is dropped by ``_build_result``.
        assert len(result.blocks) == 1
        assert called["flag"] is False, (
            "ocr_pdf_pages must not be invoked when deps are missing"
        )

    def test_ocr_does_not_overwrite_meaningful_native_text(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If pypdf extracted plenty of text, OCR shouldn't run —
        even less, it shouldn't overwrite the native output."""
        from unittest.mock import patch
        from anonymizer.extractors.pdf import PdfExtractor

        pdf_path = tmp_path / "all_native.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        mock_reader = self._make_mock_reader(
            ["Texto longo o suficiente pra não ativar fallback OCR."]
        )

        def boom(*a: Any, **k: Any) -> dict[int, str]:
            raise AssertionError("OCR should not be invoked")

        monkeypatch.setattr(ocr_mod, "is_available", lambda: True)
        monkeypatch.setattr(ocr_mod, "ocr_pdf_pages", boom)

        with patch(
            "anonymizer.extractors.pdf.PdfReader", return_value=mock_reader
        ):
            result = PdfExtractor().extract(pdf_path)

        assert len(result.blocks) == 1
        assert "Texto longo" in result.blocks[0].text
