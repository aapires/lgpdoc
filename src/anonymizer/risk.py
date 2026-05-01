"""Risk scoring and decision logic for verification.

Scoring is purely additive: each finding contributes a configurable weight.
The decision derived from the level always lands the document in the review
flow — there is no auto-block. Critical level is a visual signal for the
reviewer; it does not gate the workflow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Level = Literal["low", "medium", "high", "critical"]
Decision = Literal["auto_approve", "sample_review", "manual_review"]


@dataclass(frozen=True)
class Finding:
    kind: str          # entity_type (second-pass) or rule_id
    source: str        # "second_pass" | "rule"
    start: int
    end: int
    severity: str = "medium"


@dataclass
class VerificationConfig:
    weights: dict[str, float] = field(default_factory=dict)
    thresholds: dict[str, float] = field(
        default_factory=lambda: {"medium": 10.0, "high": 30.0, "critical": 80.0}
    )
    default_weight: float = 1.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VerificationConfig":
        return cls(
            weights={k: float(v) for k, v in (data.get("weights") or {}).items()},
            thresholds={k: float(v) for k, v in (data.get("thresholds") or {}).items()}
            or {"medium": 10.0, "high": 30.0, "critical": 80.0},
            default_weight=float(data.get("default_weight", 1.0)),
        )

    @classmethod
    def default(cls) -> "VerificationConfig":
        """Sensible fallback when the policy YAML omits a verification section."""
        return cls(
            weights={
                "private_email": 5,
                "private_phone": 5,
                "private_person": 4,
                "private_address": 3,
                "private_date": 2,
                "private_url": 3,
                "account_number": 100,
                "secret": 100,
                "cpf": 50,
                "cnpj": 50,
                "cep": 5,
                "br_phone": 5,
                "email": 5,
                "jwt": 100,
                "bearer_token": 100,
                "private_key": 100,
                "api_key": 100,
            },
            thresholds={"medium": 10.0, "high": 30.0, "critical": 80.0},
        )


@dataclass
class RiskAssessment:
    score: float
    level: Level
    decision: Decision
    reasons: list[str] = field(default_factory=list)


def _level_for_score(score: float, thresholds: dict[str, float]) -> Level:
    if score >= thresholds.get("critical", float("inf")):
        return "critical"
    if score >= thresholds.get("high", float("inf")):
        return "high"
    if score >= thresholds.get("medium", float("inf")):
        return "medium"
    return "low"


def _decision_for(level: Level) -> Decision:
    if level in ("high", "critical"):
        return "manual_review"
    if level == "medium":
        return "sample_review"
    return "auto_approve"


def assess(findings: list[Finding], config: VerificationConfig) -> RiskAssessment:
    """Compute score, level and decision from a list of findings."""
    score = 0.0
    by_kind: dict[str, int] = {}

    for f in findings:
        weight = config.weights.get(f.kind, config.default_weight)
        score += weight
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1

    level = _level_for_score(score, config.thresholds)
    decision = _decision_for(level)

    reasons: list[str] = []
    for kind, count in sorted(by_kind.items()):
        weight = config.weights.get(kind, config.default_weight)
        reasons.append(
            f"{count}x {kind} (weight={weight:g}, contribution={count * weight:g})"
        )
    if not findings:
        reasons.append("No residual findings — redaction looks clean.")

    return RiskAssessment(score=score, level=level, decision=decision, reasons=reasons)
