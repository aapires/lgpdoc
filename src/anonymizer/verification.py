"""Verifier: runs a second pass + deterministic rules on already-redacted text.

Produces a serialisable VerificationReport whose ``risk_assessment`` field
encodes the final decision (auto_approve / sample_review / manual_review /
blocked).
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from .client import PrivacyFilterClient
from .risk import Finding, RiskAssessment, VerificationConfig, assess
from .rules import RuleMatch, run_all_rules

logger = logging.getLogger(__name__)


@dataclass
class VerificationReport:
    risk_assessment: RiskAssessment
    residual_spans: list[dict] = field(default_factory=list)
    rule_findings: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_assessment": asdict(self.risk_assessment),
            "residual_spans": list(self.residual_spans),
            "rule_findings": list(self.rule_findings),
        }


class Verifier:
    def __init__(self, client: PrivacyFilterClient, config: VerificationConfig) -> None:
        self._client = client
        self._config = config

    def verify(self, redacted_text: str) -> VerificationReport:
        # Pass 1: second run of the privacy-filter detector.
        residual_detected = self._client.detect(redacted_text)
        residual_spans = [
            {
                "entity_type": s.entity_type,
                "start": s.start,
                "end": s.end,
                "confidence": s.confidence,
                "source": s.source or "second_pass",
            }
            for s in residual_detected
        ]

        # Pass 2: deterministic regex/algorithmic rules.
        rule_matches: list[RuleMatch] = run_all_rules(redacted_text)
        rule_findings = [
            {
                "rule_id": m.rule_id,
                "start": m.start,
                "end": m.end,
                "severity": m.severity,
            }
            for m in rule_matches
        ]

        # Combine both into a single ``Finding`` list for scoring.
        all_findings: list[Finding] = []
        for s in residual_detected:
            all_findings.append(
                Finding(
                    kind=s.entity_type,
                    source="second_pass",
                    start=s.start,
                    end=s.end,
                )
            )
        for m in rule_matches:
            all_findings.append(
                Finding(
                    kind=m.rule_id,
                    source="rule",
                    start=m.start,
                    end=m.end,
                    severity=m.severity,
                )
            )

        risk = assess(all_findings, self._config)

        logger.info(
            "Verification done score=%.2f level=%s decision=%s "
            "residual=%d rule_findings=%d",
            risk.score,
            risk.level,
            risk.decision,
            len(residual_spans),
            len(rule_findings),
        )

        return VerificationReport(
            risk_assessment=risk,
            residual_spans=residual_spans,
            rule_findings=rule_findings,
        )
