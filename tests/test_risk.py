"""Tests for risk scoring and decision logic."""
from __future__ import annotations

import pytest

from anonymizer.risk import Finding, VerificationConfig, assess


def _f(kind: str, source: str = "rule") -> Finding:
    return Finding(kind=kind, source=source, start=0, end=5)


@pytest.fixture()
def config() -> VerificationConfig:
    return VerificationConfig(
        weights={
            "private_email": 5,
            "private_person": 4,
            "cpf": 50,
            "secret": 100,
            "account_number": 100,
            "jwt": 100,
        },
        thresholds={"medium": 10.0, "high": 30.0, "critical": 80.0},
        default_weight=1.0,
    )


# ---------------------------------------------------------------------------
# Score & level
# ---------------------------------------------------------------------------

def test_no_findings_low_auto_approve(config: VerificationConfig) -> None:
    r = assess([], config)
    assert r.score == 0.0
    assert r.level == "low"
    assert r.decision == "auto_approve"


def test_single_low_weight_stays_low(config: VerificationConfig) -> None:
    r = assess([_f("private_person")], config)
    assert r.score == 4.0
    assert r.level == "low"
    assert r.decision == "auto_approve"


def test_medium_threshold_triggers_sample_review(config: VerificationConfig) -> None:
    # 3 emails @ 5 = 15 → medium
    r = assess([_f("private_email") for _ in range(3)], config)
    assert r.score == 15.0
    assert r.level == "medium"
    assert r.decision == "sample_review"


def test_high_threshold_triggers_manual_review(config: VerificationConfig) -> None:
    # 1 cpf @ 50 = 50 → high
    r = assess([_f("cpf")], config)
    assert r.score == 50.0
    assert r.level == "high"
    assert r.decision == "manual_review"


def test_critical_score_routes_to_manual_review(config: VerificationConfig) -> None:
    # 2 cpf @ 50 = 100 → critical level — but no auto-block: still goes to review.
    r = assess([_f("cpf"), _f("cpf")], config)
    assert r.score == 100.0
    assert r.level == "critical"
    assert r.decision == "manual_review"


def test_secret_keeps_review_flow(config: VerificationConfig) -> None:
    # A single secret hit (weight 100) puts the document at critical level,
    # but the document still flows through manual_review — never blocked.
    r = assess([_f("secret")], config)
    assert r.level == "critical"
    assert r.decision == "manual_review"


def test_account_number_routes_to_manual_review(config: VerificationConfig) -> None:
    r = assess([_f("account_number", source="second_pass")], config)
    assert r.decision == "manual_review"


# ---------------------------------------------------------------------------
# Reasons
# ---------------------------------------------------------------------------

def test_reasons_include_per_kind_breakdown(config: VerificationConfig) -> None:
    r = assess([_f("private_email"), _f("private_email")], config)
    joined = " ".join(r.reasons)
    assert "private_email" in joined
    assert "weight=5" in joined


def test_reasons_clean_when_no_findings(config: VerificationConfig) -> None:
    r = assess([], config)
    assert any("clean" in reason.lower() for reason in r.reasons)


# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------

def test_default_config_secret_routes_to_manual_review() -> None:
    cfg = VerificationConfig.default()
    r = assess([_f("secret")], cfg)
    assert r.level == "critical"
    assert r.decision == "manual_review"


def test_default_config_jwt_routes_to_manual_review() -> None:
    cfg = VerificationConfig.default()
    r = assess([_f("jwt")], cfg)
    assert r.level == "critical"
    assert r.decision == "manual_review"


def test_default_config_no_findings() -> None:
    cfg = VerificationConfig.default()
    r = assess([], cfg)
    assert r.decision == "auto_approve"


# ---------------------------------------------------------------------------
# from_dict
# ---------------------------------------------------------------------------

def test_from_dict_parses_yaml_shape() -> None:
    cfg = VerificationConfig.from_dict({
        "weights": {"foo": 7.5, "bar": "3"},
        "thresholds": {"medium": 5, "high": 15, "critical": 50},
        "default_weight": 0.5,
    })
    assert cfg.weights == {"foo": 7.5, "bar": 3.0}
    assert cfg.thresholds == {"medium": 5.0, "high": 15.0, "critical": 50.0}
    assert cfg.default_weight == 0.5


def test_unknown_kind_uses_default_weight() -> None:
    cfg = VerificationConfig(
        weights={},
        thresholds={"medium": 10.0, "high": 30.0, "critical": 80.0},
        default_weight=2.0,
    )
    r = assess([_f("unknown_kind")], cfg)
    assert r.score == 2.0
