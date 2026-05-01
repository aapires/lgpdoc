"""Tests for DocumentPipeline using synthetic files and MockPrivacyFilterClient."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from anonymizer.client import MockPrivacyFilterClient
from anonymizer.extractors.base import UnsupportedFormatError
from anonymizer.pipeline import DEFAULT_MAX_BYTES, DocumentPipeline, FileTooLargeError
from anonymizer.policy import Policy

_POLICY_PATH = Path(__file__).parent.parent / "policies" / "default.yaml"


@pytest.fixture()
def policy() -> Policy:
    return Policy.from_yaml(_POLICY_PATH)


@pytest.fixture()
def client() -> MockPrivacyFilterClient:
    return MockPrivacyFilterClient()


@pytest.fixture()
def pipeline(policy: Policy, client: MockPrivacyFilterClient, tmp_path: Path) -> DocumentPipeline:
    return DocumentPipeline(
        client=client,
        policy=policy,
        output_dir=tmp_path / "out",
    )


# ---------------------------------------------------------------------------
# 1. Full pipeline run on TXT
# ---------------------------------------------------------------------------

def test_pipeline_txt_produces_four_artefacts(
    pipeline: DocumentPipeline, synthetic_txt: Path, tmp_path: Path
) -> None:
    result = pipeline.run(synthetic_txt, policy_path=str(_POLICY_PATH))
    out = tmp_path / "out"
    assert (out / "redacted.txt").exists()
    assert (out / "spans.json").exists()
    assert (out / "job_metadata.json").exists()
    assert (out / "verification_report.json").exists()


def test_pipeline_txt_redacted_text_has_no_pii(
    pipeline: DocumentPipeline, synthetic_txt: Path
) -> None:
    result = pipeline.run(synthetic_txt)
    # The mock client detects the name — it should be replaced
    assert "Jane Doe" not in result.redacted_text
    assert "jane.doe@synthetic-example.org" not in result.redacted_text


def test_pipeline_txt_stats_populated(
    pipeline: DocumentPipeline, synthetic_txt: Path
) -> None:
    result = pipeline.run(synthetic_txt)
    assert result.metadata.stats  # at least one entity type detected


# ---------------------------------------------------------------------------
# 2. Full pipeline run on DOCX
# ---------------------------------------------------------------------------

def test_pipeline_docx_runs(
    pipeline: DocumentPipeline, synthetic_docx: Path
) -> None:
    result = pipeline.run(synthetic_docx)
    assert result.redacted_text
    assert len(result.applied_spans) > 0


# ---------------------------------------------------------------------------
# 3. Full pipeline run on XLSX
# ---------------------------------------------------------------------------

def test_pipeline_xlsx_runs(
    pipeline: DocumentPipeline, synthetic_xlsx: Path
) -> None:
    result = pipeline.run(synthetic_xlsx)
    assert result.redacted_text
    assert len(result.applied_spans) > 0


# ---------------------------------------------------------------------------
# 4. job_metadata.json structure
# ---------------------------------------------------------------------------

def test_job_metadata_fields(
    pipeline: DocumentPipeline, synthetic_txt: Path, tmp_path: Path
) -> None:
    result = pipeline.run(synthetic_txt, policy_path=str(_POLICY_PATH))
    meta_path = tmp_path / "out" / "job_metadata.json"
    meta = json.loads(meta_path.read_text())

    assert "job_id" in meta
    assert len(meta["job_id"]) == 36          # UUID4
    assert "file_hash" in meta
    assert len(meta["file_hash"]) == 64        # SHA-256 hex
    assert meta["format"] == "txt"
    assert meta["block_count"] >= 1
    assert "created_at" in meta
    assert "stats" in meta


# ---------------------------------------------------------------------------
# 5. spans.json has document-level offsets
# ---------------------------------------------------------------------------

def test_spans_json_document_level_offsets(
    pipeline: DocumentPipeline, synthetic_txt: Path, tmp_path: Path
) -> None:
    result = pipeline.run(synthetic_txt)
    spans_path = tmp_path / "out" / "spans.json"
    spans = json.loads(spans_path.read_text())

    assert isinstance(spans, list)
    for span in spans:
        assert "block_id" in span
        assert "doc_start" in span
        assert "doc_end" in span
        assert span["doc_start"] >= 0
        assert span["doc_end"] > span["doc_start"]


# ---------------------------------------------------------------------------
# 6. Unsupported extension rejected
# ---------------------------------------------------------------------------

def test_unsupported_extension_rejected(
    pipeline: DocumentPipeline, tmp_path: Path
) -> None:
    bad = tmp_path / "file.exe"
    bad.write_bytes(b"binary content")
    with pytest.raises(UnsupportedFormatError, match="not supported"):
        pipeline.run(bad)


# ---------------------------------------------------------------------------
# 7. File too large rejected
# ---------------------------------------------------------------------------

def test_file_too_large_rejected(
    policy: Policy, client: MockPrivacyFilterClient, tmp_path: Path
) -> None:
    p = DocumentPipeline(
        client=client,
        policy=policy,
        output_dir=tmp_path / "out",
        max_bytes=10,
    )
    big = tmp_path / "big.txt"
    big.write_text("x" * 100, encoding="utf-8")
    with pytest.raises(FileTooLargeError):
        p.run(big)


# ---------------------------------------------------------------------------
# 8. Document with no PII produces unchanged text and empty spans
# ---------------------------------------------------------------------------

def test_no_pii_document(
    pipeline: DocumentPipeline, tmp_path: Path
) -> None:
    clean = tmp_path / "clean.txt"
    clean.write_text(
        "The quick brown fox.\n\nNo personal information here.", encoding="utf-8"
    )
    result = pipeline.run(clean)
    assert result.applied_spans == []
    assert result.metadata.stats == {}


# ---------------------------------------------------------------------------
# 9. job_id is unique per run
# ---------------------------------------------------------------------------

def test_job_id_unique_per_run(
    pipeline: DocumentPipeline, synthetic_txt: Path
) -> None:
    r1 = pipeline.run(synthetic_txt)
    r2 = pipeline.run(synthetic_txt)
    assert r1.job_id != r2.job_id


# ---------------------------------------------------------------------------
# 10. file_hash is stable for identical content
# ---------------------------------------------------------------------------

def test_file_hash_stable(
    pipeline: DocumentPipeline, synthetic_txt: Path
) -> None:
    r1 = pipeline.run(synthetic_txt)
    r2 = pipeline.run(synthetic_txt)
    assert r1.metadata.file_hash == r2.metadata.file_hash


# ---------------------------------------------------------------------------
# Verification integration
# ---------------------------------------------------------------------------

def test_verification_report_written(
    pipeline: DocumentPipeline, synthetic_txt: Path, tmp_path: Path
) -> None:
    pipeline.run(synthetic_txt)
    report_path = tmp_path / "out" / "verification_report.json"
    report = json.loads(report_path.read_text())

    assert "risk_assessment" in report
    assert "residual_spans" in report
    assert "rule_findings" in report
    risk = report["risk_assessment"]
    assert risk["level"] in {"low", "medium", "high", "critical"}
    assert risk["decision"] in {
        "auto_approve",
        "sample_review",
        "manual_review",
        "blocked",
    }


def test_verification_attached_to_result(
    pipeline: DocumentPipeline, synthetic_txt: Path
) -> None:
    result = pipeline.run(synthetic_txt)
    assert result.verification is not None
    assert result.verification.risk_assessment.decision in {
        "auto_approve",
        "sample_review",
        "manual_review",
        "blocked",
    }


def test_pipeline_critical_secret_awaits_manual_review(
    pipeline: DocumentPipeline, tmp_path: Path
) -> None:
    # The mock client (regex-based) does not redact JWTs; the deterministic
    # rules catch it during verification, flag it as critical, and route the
    # document to manual review (never blocked).
    leak = tmp_path / "leak.txt"
    leak.write_text(
        "Some prose here.\n\n"
        "Token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abcdefghij",
        encoding="utf-8",
    )
    result = pipeline.run(leak)
    assert result.verification.risk_assessment.level == "critical"
    assert result.verification.risk_assessment.decision == "manual_review"


def test_applied_spans_have_redacted_positions(
    pipeline: DocumentPipeline, synthetic_txt: Path
) -> None:
    """Each applied span must carry its position in the final redacted text."""
    result = pipeline.run(synthetic_txt)
    redacted_text = result.redacted_text
    for span in result.applied_spans:
        rs, re_ = span["redacted_start"], span["redacted_end"]
        # Position is valid…
        assert 0 <= rs < re_ <= len(redacted_text)
        # …and the slice of the redacted text matches the recorded replacement.
        assert redacted_text[rs:re_] == span["replacement"]


def test_applied_spans_carry_detection_source(
    pipeline: DocumentPipeline, synthetic_txt: Path
) -> None:
    """Each applied span must record which detector produced it. Without
    this the reviewer can't tell what came from the model vs. from regex."""
    result = pipeline.run(synthetic_txt)
    assert result.applied_spans, "fixture should produce at least one span"
    for s in result.applied_spans:
        # Either a known detector source or None for legacy spans
        assert "source" in s
        assert "confidence" in s


def test_applied_spans_carry_original_text_and_context(
    pipeline: DocumentPipeline, synthetic_txt: Path
) -> None:
    """Reviewers need to see the original PII value to judge each detection."""
    result = pipeline.run(synthetic_txt)
    assert result.applied_spans, "fixture should produce at least one span"
    for s in result.applied_spans:
        assert "original_text" in s and s["original_text"]
        assert "original_context_before" in s
        assert "original_context_after" in s
        # Surrounding context must not contain the original text — they are
        # neighbouring slices.
        assert s["original_text"] not in (
            s["original_context_before"] or ""
        )
        assert s["original_text"] not in (
            s["original_context_after"] or ""
        )


def test_clean_document_auto_approves(
    pipeline: DocumentPipeline, tmp_path: Path
) -> None:
    clean = tmp_path / "clean.txt"
    clean.write_text("Lorem ipsum dolor sit amet.", encoding="utf-8")
    result = pipeline.run(clean)
    assert result.verification.risk_assessment.decision == "auto_approve"
    assert result.verification.risk_assessment.score == 0.0
