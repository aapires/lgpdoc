"""Tests for Verifier — second pass + rules + risk assessment."""
from __future__ import annotations

from anonymizer.client import MockPrivacyFilterClient
from anonymizer.risk import VerificationConfig
from anonymizer.verification import Verifier


def test_verifier_clean_text_auto_approves() -> None:
    config = VerificationConfig(
        weights={},
        thresholds={"medium": 10.0, "high": 30.0, "critical": 80.0},
    )
    verifier = Verifier(MockPrivacyFilterClient(), config)
    report = verifier.verify("The quick brown fox jumps over the lazy dog.")

    assert report.risk_assessment.decision == "auto_approve"
    assert report.residual_spans == []
    assert report.rule_findings == []


def test_verifier_detects_residual_email_via_second_pass() -> None:
    config = VerificationConfig.default()
    verifier = Verifier(MockPrivacyFilterClient(), config)
    # Redacted text that still leaks an email address
    report = verifier.verify("Contact: leaked@example.org")

    assert any(
        s["entity_type"] == "private_email" for s in report.residual_spans
    )


def test_verifier_jwt_routes_to_manual_review() -> None:
    config = VerificationConfig.default()
    verifier = Verifier(MockPrivacyFilterClient(), config)
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abcdefghij"
    report = verifier.verify(f"oops jwt={jwt}")

    # JWT now triggers critical level → manual review (no auto-block)
    assert report.risk_assessment.level == "critical"
    assert report.risk_assessment.decision == "manual_review"
    assert any(f["rule_id"] == "jwt" for f in report.rule_findings)


def test_verifier_combines_residual_and_rules() -> None:
    config = VerificationConfig.default()
    verifier = Verifier(MockPrivacyFilterClient(), config)
    text = "leaked@example.org Bearer abcdef1234567890ghijklmnop"
    report = verifier.verify(text)

    assert len(report.residual_spans) >= 1
    assert any(f["rule_id"] == "bearer_token" for f in report.rule_findings)
    # Bearer token now lands at manual_review with critical level — never blocked
    assert report.risk_assessment.level == "critical"
    assert report.risk_assessment.decision == "manual_review"


def test_report_to_dict_is_json_safe() -> None:
    import json

    config = VerificationConfig.default()
    verifier = Verifier(MockPrivacyFilterClient(), config)
    report = verifier.verify("Just text.")

    payload = json.dumps(report.to_dict())
    assert "risk_assessment" in payload
    assert "residual_spans" in payload
    assert "rule_findings" in payload
