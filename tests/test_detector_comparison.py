"""Tests for the OPF-vs-regex comparison core.

Synthetic spans only — these tests never instantiate the real OPF model
nor the augmented client. The goal is to exercise the comparison
algorithm's behaviour and verify nothing logs raw text or PII fragments.
"""
from __future__ import annotations

import logging

import pytest

from anonymizer.detector_comparison import (
    ComparisonItem,
    ComparisonSummary,
    DetectorComparisonReport,
    PARTIAL_OVERLAP_THRESHOLD,
    STRONG_OVERLAP_THRESHOLD,
    build_comparison_report,
    compare_spans,
)
from anonymizer.models import DetectedSpan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _opf(start: int, end: int, entity_type: str = "private_person") -> DetectedSpan:
    return DetectedSpan(
        start=start,
        end=end,
        entity_type=entity_type,
        confidence=0.9,
        source="openai_privacy_filter",
    )


def _rgx(
    start: int,
    end: int,
    entity_type: str = "private_person",
    source: str = "br_labeled_name",
) -> DetectedSpan:
    return DetectedSpan(
        start=start,
        end=end,
        entity_type=entity_type,
        confidence=0.95,
        source=source,
    )


# A short synthetic block of fake text. The literal "Foobar" / "Quux" are
# not real PII — but the privacy-log test still asserts these literals
# never appear in any log record.
SYNTH_TEXT = "Cliente: Foobar Quux. Documento gerado em 01/01/2099."
SYNTH_NAME_START = SYNTH_TEXT.index("Foobar")
SYNTH_NAME_END = SYNTH_NAME_START + len("Foobar Quux")


# ---------------------------------------------------------------------------
# compare_spans — pairing & status classification
# ---------------------------------------------------------------------------

class TestCompareSpansBoth:
    def test_identical_offsets_and_type_yields_both(self) -> None:
        opf = [_opf(10, 20, "private_person")]
        rgx = [_rgx(10, 20, "private_person")]

        items = compare_spans(opf, rgx, block_id="b1")

        assert len(items) == 1
        assert items[0].status == "both"
        assert items[0].overlap_ratio == pytest.approx(1.0)
        assert items[0].opf_span is not None
        assert items[0].regex_span is not None
        assert items[0].opf_span.entity_type == "private_person"
        assert items[0].regex_span.entity_type == "private_person"


class TestCompareSpansOpfOnly:
    def test_opf_span_with_no_regex_yields_opf_only(self) -> None:
        opf = [_opf(0, 5, "private_email")]
        rgx: list[DetectedSpan] = []

        items = compare_spans(opf, rgx, block_id="b1")

        assert len(items) == 1
        assert items[0].status == "opf_only"
        assert items[0].opf_span is not None
        assert items[0].regex_span is None
        assert items[0].overlap_ratio == 0.0


class TestCompareSpansRegexOnly:
    def test_regex_span_with_no_opf_yields_regex_only(self) -> None:
        opf: list[DetectedSpan] = []
        rgx = [_rgx(30, 41, "cpf", source="br_cpf")]

        items = compare_spans(opf, rgx, block_id="b1")

        assert len(items) == 1
        assert items[0].status == "regex_only"
        assert items[0].opf_span is None
        assert items[0].regex_span is not None
        assert items[0].regex_span.source == "br_cpf"


class TestCompareSpansPartialOverlap:
    def test_moderate_overlap_yields_partial(self) -> None:
        # OPF [10, 20], regex [15, 30] → intersection 5, union 20 → 0.25
        # That's below the partial threshold (0.30) so they don't pair.
        opf = [_opf(10, 20)]
        rgx = [_rgx(15, 30)]

        items = compare_spans(opf, rgx, block_id="b1")

        # No pairing: each becomes its own *_only item.
        statuses = sorted(i.status for i in items)
        assert statuses == ["opf_only", "regex_only"]

    def test_overlap_within_partial_band_pairs(self) -> None:
        # OPF [10, 20], regex [13, 22] → inter 7, union 12 → ~0.583
        # 0.30 <= 0.583 < 0.90 → partial_overlap
        opf = [_opf(10, 20, "private_person")]
        rgx = [_rgx(13, 22, "private_person")]

        items = compare_spans(opf, rgx, block_id="b1")

        assert len(items) == 1
        assert items[0].status == "partial_overlap"
        assert PARTIAL_OVERLAP_THRESHOLD <= items[0].overlap_ratio < STRONG_OVERLAP_THRESHOLD


class TestCompareSpansTypeConflict:
    def test_strong_overlap_with_different_types_yields_conflict(self) -> None:
        opf = [_opf(0, 14, "account_number")]
        rgx = [_rgx(0, 14, "cpf", source="br_cpf")]

        items = compare_spans(opf, rgx, block_id="b1")

        assert len(items) == 1
        assert items[0].status == "type_conflict"
        assert items[0].overlap_ratio >= STRONG_OVERLAP_THRESHOLD
        assert items[0].opf_span is not None
        assert items[0].regex_span is not None
        assert items[0].opf_span.entity_type == "account_number"
        assert items[0].regex_span.entity_type == "cpf"


# ---------------------------------------------------------------------------
# Greedy pairing — a regex span must not be consumed by multiple OPF spans
# ---------------------------------------------------------------------------

class TestRegexNotConsumedTwice:
    def test_two_opf_spans_compete_for_one_regex_best_overlap_wins(self) -> None:
        # Two OPF spans both touching one regex span.
        # OPF #0 [10, 20] perfectly aligns with regex.
        # OPF #1 [12, 30] only partially overlaps regex [10, 20].
        # Greedy pairing must give the regex to OPF #0 (highest overlap)
        # and leave OPF #1 unpaired.
        opf = [
            _opf(10, 20, "private_person"),
            _opf(12, 30, "private_person"),
        ]
        rgx = [_rgx(10, 20, "private_person")]

        items = compare_spans(opf, rgx, block_id="b1")

        # Exactly one paired item + one opf_only for the loser.
        paired = [i for i in items if i.opf_span is not None and i.regex_span is not None]
        assert len(paired) == 1
        assert paired[0].status == "both"
        assert paired[0].opf_span is not None
        assert paired[0].opf_span.start == 10
        assert paired[0].opf_span.end == 20

        opf_only = [i for i in items if i.status == "opf_only"]
        assert len(opf_only) == 1
        assert opf_only[0].opf_span is not None
        assert opf_only[0].opf_span.start == 12
        assert opf_only[0].opf_span.end == 30

        # And every regex span appears at most once across all items.
        regex_appearances = sum(1 for i in items if i.regex_span is not None)
        assert regex_appearances == 1


# ---------------------------------------------------------------------------
# build_comparison_report — global summary
# ---------------------------------------------------------------------------

class TestBuildReportGlobalSummary:
    def test_summary_counts_match_item_statuses(self) -> None:
        # Block 1: one "both"
        items = compare_spans(
            opf_spans=[_opf(0, 5, "private_email")],
            regex_spans=[_rgx(0, 5, "private_email", source="email_rgx")],
            block_id="b1",
        )
        # Block 2: one type_conflict + one opf_only + one regex_only
        items += compare_spans(
            opf_spans=[
                _opf(0, 14, "account_number"),
                _opf(40, 50, "private_phone"),
            ],
            regex_spans=[
                _rgx(0, 14, "cpf", source="br_cpf"),
                _rgx(80, 91, "cpf", source="br_cpf"),
            ],
            block_id="b2",
        )

        report = build_comparison_report(job_id="job-x", block_results=items)

        assert isinstance(report, DetectorComparisonReport)
        assert report.summary.total == 4
        assert report.summary.both == 1
        assert report.summary.type_conflict == 1
        assert report.summary.opf_only == 1
        assert report.summary.regex_only == 1
        assert report.summary.partial_overlap == 0

    def test_items_sorted_by_block_then_start(self) -> None:
        items = compare_spans(
            opf_spans=[_opf(50, 60, "private_person")],
            regex_spans=[_rgx(50, 60, "private_person")],
            block_id="b2",
        )
        items += compare_spans(
            opf_spans=[_opf(10, 20, "private_email")],
            regex_spans=[_rgx(10, 20, "private_email", source="email_rgx")],
            block_id="b1",
        )

        report = build_comparison_report(job_id="job-x", block_results=items)

        # Sorted: ("b1", 10) then ("b2", 50)
        assert [it.block_id for it in report.items] == ["b1", "b2"]


# ---------------------------------------------------------------------------
# build_comparison_report — per-entity-type summary
# ---------------------------------------------------------------------------

class TestBuildReportPerType:
    def test_per_type_summary_buckets_correctly(self) -> None:
        items: list[ComparisonItem] = []
        # private_person: 2 both
        items += compare_spans(
            opf_spans=[_opf(0, 11, "private_person"), _opf(20, 31, "private_person")],
            regex_spans=[_rgx(0, 11, "private_person"), _rgx(20, 31, "private_person")],
            block_id="b1",
        )
        # cpf: 1 type_conflict (regex says cpf, opf says account_number) + 1 regex_only
        items += compare_spans(
            opf_spans=[_opf(40, 54, "account_number")],
            regex_spans=[
                _rgx(40, 54, "cpf", source="br_cpf"),
                _rgx(80, 91, "cpf", source="br_cpf"),
            ],
            block_id="b2",
        )
        # private_email: 1 opf_only
        items += compare_spans(
            opf_spans=[_opf(100, 120, "private_email")],
            regex_spans=[],
            block_id="b3",
        )

        report = build_comparison_report(job_id="job-x", block_results=items)

        by_type = {ec.entity_type: ec.summary for ec in report.by_entity_type}

        # Buckets: regex span wins when both are present (so type_conflict
        # counts under "cpf", not "account_number").
        assert "private_person" in by_type
        assert by_type["private_person"] == ComparisonSummary(
            total=2, both=2, opf_only=0, regex_only=0, partial_overlap=0, type_conflict=0
        )
        assert "cpf" in by_type
        assert by_type["cpf"] == ComparisonSummary(
            total=2, both=0, opf_only=0, regex_only=1, partial_overlap=0, type_conflict=1
        )
        assert "private_email" in by_type
        assert by_type["private_email"] == ComparisonSummary(
            total=1, both=0, opf_only=1, regex_only=0, partial_overlap=0, type_conflict=0
        )
        # "account_number" should NOT appear — the type_conflict is bucketed
        # under the regex's "cpf" only.
        assert "account_number" not in by_type

    def test_per_type_entries_sorted_alphabetically(self) -> None:
        items: list[ComparisonItem] = []
        items += compare_spans(
            opf_spans=[_opf(0, 5, "private_email")],
            regex_spans=[_rgx(0, 5, "private_email", source="email_rgx")],
            block_id="b1",
        )
        items += compare_spans(
            opf_spans=[_opf(10, 20, "private_person")],
            regex_spans=[_rgx(10, 20, "private_person")],
            block_id="b1",
        )
        report = build_comparison_report(job_id="job-x", block_results=items)
        types = [ec.entity_type for ec in report.by_entity_type]
        assert types == sorted(types)


# ---------------------------------------------------------------------------
# Privacy: synthetic span text must not leak into log records
# ---------------------------------------------------------------------------

class TestNoSensitiveLogging:
    def test_logs_do_not_contain_synthetic_text(self, caplog: pytest.LogCaptureFixture) -> None:
        opf = [
            _opf(SYNTH_NAME_START, SYNTH_NAME_END, "private_person"),
        ]
        rgx = [
            _rgx(
                SYNTH_NAME_START,
                SYNTH_NAME_END,
                "private_person",
                source="br_labeled_name",
            ),
        ]

        with caplog.at_level(logging.DEBUG, logger="anonymizer.detector_comparison"):
            items = compare_spans(opf, rgx, block_id="b1", text=SYNTH_TEXT)
            report = build_comparison_report(job_id="job-x", block_results=items)

        # The previews should still have been populated on the report —
        # they're just never logged.
        assert items[0].opf_span is not None
        assert items[0].opf_span.text_preview == "Foobar Quux"
        assert report.summary.total == 1

        # No log record may contain the synthetic span text or surrounding
        # synthetic words.
        forbidden = ("Foobar", "Quux", "Cliente", SYNTH_TEXT)
        for record in caplog.records:
            msg = record.getMessage()
            for token in forbidden:
                assert token not in msg, (
                    f"log record leaked synthetic text {token!r} via {record.name!r}: {msg!r}"
                )
