"""Markdown formatting helpers shared by extractors that surface
tables (DOCX, XLSX). Pure functions — no I/O, no logging.

Why output Markdown:

* LLMs (the typical destination of pseudonymized text) handle
  Markdown tables natively; preserving table structure means downstream
  analysis doesn't lose the column relationships.
* Markers like ``[PESSOA_0001]`` survive inside table cells without
  any special handling — they're just text.
* The format is human-readable and round-trips through plain UTF-8.

Cells with pipe characters or newlines are sanitised so the resulting
table is valid Markdown:

* ``|``  → ``\\|``  (so the cell boundary stays a visual ``|``)
* ``\\n`` / ``\\r`` → space
"""
from __future__ import annotations

import re

_ESCAPE_PIPE_RE = re.compile(r"\|")
_FLATTEN_WS_RE = re.compile(r"[\r\n]+")


def _sanitise_cell(value: object) -> str:
    """Return a Markdown-safe cell string. ``None`` becomes empty;
    everything else is stringified, newlines flattened, pipes escaped."""
    if value is None:
        return ""
    text = str(value)
    text = _FLATTEN_WS_RE.sub(" ", text)
    text = _ESCAPE_PIPE_RE.sub(r"\|", text)
    return text.strip()


def to_markdown_table(rows: list[list[object]]) -> str:
    """Render ``rows`` as a GitHub-flavoured Markdown table.

    Header row defaults to the first row of ``rows`` — that's the
    convention for Excel sheets and Word tables alike. If you don't
    want a header, pass an empty first row (``[""] * n``) and the
    separator will still align the visible header with all-empty cells.

    Returns an empty string when ``rows`` is empty (nothing to render).
    Short rows are padded with empty cells so the table is rectangular.
    """
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    if width == 0:
        return ""

    def _row_to_md(row: list[object]) -> str:
        cells = [_sanitise_cell(c) for c in row]
        cells.extend([""] * (width - len(cells)))
        return "| " + " | ".join(cells) + " |"

    out_lines: list[str] = []
    out_lines.append(_row_to_md(rows[0]))
    out_lines.append("| " + " | ".join(["---"] * width) + " |")
    for row in rows[1:]:
        out_lines.append(_row_to_md(row))
    return "\n".join(out_lines)
