from __future__ import annotations

from pathlib import Path

from .base import BaseExtractor, UnsupportedFormatError
from ._markdown import to_markdown_table
from ..document_models import ExtractionResult


class XlsExtractor(BaseExtractor):
    """Extracts text from legacy XLS files (Excel 97–2003 binary).

    Mirrors :class:`XlsxExtractor`: each sheet becomes a single
    DocumentBlock containing the sheet rendered as a GitHub-flavoured
    Markdown table, with the first row treated as the header. Empty
    sheets are skipped, trailing empty rows are dropped.

    Driven by ``xlrd >= 2.0`` (the modern ``xlrd`` releases dropped
    ``.xlsx`` support and only handle ``.xls`` — that is exactly what
    we want here, with ``XlsxExtractor`` covering the modern format).
    """

    supported_extensions = frozenset({".xls"})

    def extract(self, path: Path) -> ExtractionResult:
        try:
            import xlrd
        except ImportError as exc:
            raise RuntimeError(
                "xlrd is required for XLS extraction. "
                "Install it with: pip install 'xlrd>=2.0'"
            ) from exc

        try:
            wb = xlrd.open_workbook(str(path), on_demand=True)
        except Exception as exc:
            raise UnsupportedFormatError(
                f"{path.name!r} could not be parsed as an XLS file: {exc}"
            ) from exc

        raw: list[tuple[int | None, str]] = []
        try:
            for sheet_index in range(wb.nsheets):
                ws = wb.sheet_by_index(sheet_index)
                rows: list[list[object]] = []
                for r in range(ws.nrows):
                    row = list(ws.row_values(r))
                    if any(c is not None and str(c).strip() for c in row):
                        rows.append(row)
                if not rows:
                    wb.unload_sheet(sheet_index)
                    continue
                rendered = to_markdown_table(rows)
                if rendered:
                    raw.append((sheet_index + 1, rendered))
                wb.unload_sheet(sheet_index)
        finally:
            wb.release_resources()

        return self._build_result(raw)
