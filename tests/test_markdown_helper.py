"""Pure unit tests for the Markdown helper used by DOCX/XLSX extractors."""
from __future__ import annotations

import pytest

from anonymizer.extractors._markdown import _sanitise_cell, to_markdown_table


class TestSanitiseCell:
    def test_none_becomes_empty(self) -> None:
        assert _sanitise_cell(None) == ""

    def test_pipe_is_escaped(self) -> None:
        assert _sanitise_cell("a|b") == r"a\|b"

    def test_newlines_flatten_to_spaces(self) -> None:
        assert _sanitise_cell("a\nb\r\nc") == "a b c"

    def test_int_is_stringified(self) -> None:
        assert _sanitise_cell(42) == "42"

    def test_strip_whitespace(self) -> None:
        assert _sanitise_cell("  hello  ") == "hello"


class TestToMarkdownTable:
    def test_empty_input_yields_empty_string(self) -> None:
        assert to_markdown_table([]) == ""

    def test_two_by_two_table(self) -> None:
        out = to_markdown_table(
            [["Nome", "Idade"], ["Joao", 30], ["Maria", 25]]
        )
        lines = out.split("\n")
        assert lines == [
            "| Nome | Idade |",
            "| --- | --- |",
            "| Joao | 30 |",
            "| Maria | 25 |",
        ]

    def test_short_row_is_padded(self) -> None:
        """Sheets with irregular rows must still produce a rectangular
        table — short rows pad with empty cells so column alignment
        survives."""
        out = to_markdown_table(
            [["A", "B", "C"], ["x", "y"], ["p", "q", "r", "s"]]
        )
        lines = out.split("\n")
        # Width = 4 (max row length)
        assert lines[0].count("|") == 5  # 4 cells → 5 pipes
        assert lines[1] == "| --- | --- | --- | --- |"
        # Padded row
        assert lines[2] == "| x | y |  |  |"
        # Long row truncated? No, longer rows aren't truncated; the
        # whole table widens to 4. Check that all rows have width 4.
        for line in lines[2:]:
            assert line.count("|") == 5

    def test_pipes_inside_cells_are_escaped(self) -> None:
        import re as _re

        out = to_markdown_table([["pipe|col"], [r"a|b"]])
        # Both rows must contain the escaped \| (not break the table).
        assert "pipe\\|col" in out
        assert "a\\|b" in out
        # And the line still parses as a 1-column table — count pipes
        # that are NOT preceded by a backslash; should be 2 per line
        # (the leading and trailing column separators).
        unescaped = _re.compile(r"(?<!\\)\|")
        for line in out.split("\n"):
            assert len(unescaped.findall(line)) == 2, line

    def test_marker_tokens_pass_through(self) -> None:
        """Pseudonymization markers contain no pipes or newlines and
        must survive the helper unmodified."""
        out = to_markdown_table(
            [["Nome", "CPF"], ["[PESSOA_0001]", "[CPF_0001]"]]
        )
        assert "[PESSOA_0001]" in out
        assert "[CPF_0001]" in out
