"""Unit tests for Redactor — all fixtures are synthetic."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from anonymizer.models import DetectedSpan
from anonymizer.policy import EntityPolicy, Policy
from anonymizer.redactor import Redactor

POLICY_PATH = Path(__file__).parent.parent / "policies" / "default.yaml"


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def policy() -> Policy:
    """Default policy from disk (uses ``indexed`` for everything)."""
    return Policy.from_yaml(POLICY_PATH)


@pytest.fixture()
def redactor(policy: Policy) -> Redactor:
    return Redactor(policy)


def make_policy(entities: dict[str, dict[str, Any]]) -> Policy:
    """Build an in-memory Policy from a small dict — used to test specific
    strategies without depending on the default YAML."""
    return Policy(
        {name: EntityPolicy(name, cfg) for name, cfg in entities.items()}
    )


def span(start: int, end: int, entity_type: str) -> DetectedSpan:
    return DetectedSpan(start=start, end=end, entity_type=entity_type, confidence=0.9)


# ---------------------------------------------------------------------------
# 1. Default policy uses indexed — produces [LABEL_NN]
# ---------------------------------------------------------------------------

def test_default_email_uses_indexed_placeholder(redactor: Redactor) -> None:
    text = "Contact user@example.com for info"
    spans = [span(8, 24, "private_email")]
    result = redactor.redact(text, spans)

    assert "[EMAIL_01]" in result.redacted_text
    assert result.stats == {"private_email": 1}
    assert result.applied_spans[0].strategy == "indexed"


def test_default_address_uses_indexed_placeholder(redactor: Redactor) -> None:
    text = "Lives at 42 Synthetic Street, Faketown"
    spans = [span(9, 38, "private_address")]
    result = redactor.redact(text, spans)

    assert "[ENDERECO_01]" in result.redacted_text
    assert "42 Synthetic Street" not in result.redacted_text


def test_default_person_uses_nome_placeholder(redactor: Redactor) -> None:
    text = "Hello Jane Doe, welcome."
    spans = [span(6, 14, "private_person")]
    result = redactor.redact(text, spans)
    assert "[PESSOA_01]" in result.redacted_text
    assert "Jane Doe" not in result.redacted_text


# ---------------------------------------------------------------------------
# 2. Overlapping spans — only the wider one survives
# ---------------------------------------------------------------------------

def test_overlapping_spans_resolved(redactor: Redactor) -> None:
    text = "Call 555-0100-FAKE now"
    spans = [
        span(5, 20, "private_phone"),   # wider
        span(5, 13, "private_phone"),   # narrower
    ]
    result = redactor.redact(text, spans)
    assert len(result.applied_spans) == 1
    assert result.stats["private_phone"] == 1


# ---------------------------------------------------------------------------
# 3. Indexed strategy — same value gets same index across the document
# ---------------------------------------------------------------------------

class TestIndexedStrategy:
    def test_assigns_sequential_indices(self, redactor: Redactor) -> None:
        text = "Alice and Bob and Carol"
        spans = [
            span(0, 5, "private_person"),
            span(10, 13, "private_person"),
            span(18, 23, "private_person"),
        ]
        result = redactor.redact(text, spans)
        assert result.redacted_text == "[PESSOA_01] and [PESSOA_02] and [PESSOA_03]"

    def test_same_value_gets_same_index(self, redactor: Redactor) -> None:
        text = "Alice and Alice and Bob"
        spans = [
            span(0, 5, "private_person"),
            span(10, 15, "private_person"),
            span(20, 23, "private_person"),
        ]
        result = redactor.redact(text, spans)
        assert result.redacted_text == "[PESSOA_01] and [PESSOA_01] and [PESSOA_02]"

    def test_case_and_whitespace_insensitive_dedup(self, redactor: Redactor) -> None:
        # "ALICE", "Alice" and "alice" should all collapse to NOME_01
        text = "ALICE Alice alice"
        spans = [
            span(0, 5, "private_person"),
            span(6, 11, "private_person"),
            span(12, 17, "private_person"),
        ]
        result = redactor.redact(text, spans)
        assert result.redacted_text == "[PESSOA_01] [PESSOA_01] [PESSOA_01]"

    def test_per_entity_type_counters_are_independent(
        self, redactor: Redactor
    ) -> None:
        # Counter for private_person and private_email start independently.
        text = "Alice alice@example.com Bob bob@example.com"
        spans = [
            span(0, 5, "private_person"),
            span(6, 23, "private_email"),
            span(24, 27, "private_person"),
            span(28, 43, "private_email"),
        ]
        result = redactor.redact(text, spans)
        assert "[PESSOA_01]" in result.redacted_text
        assert "[PESSOA_02]" in result.redacted_text
        assert "[EMAIL_01]" in result.redacted_text
        assert "[EMAIL_02]" in result.redacted_text

    def test_correlation_persists_across_redact_calls(
        self, redactor: Redactor
    ) -> None:
        # The pipeline runs Redactor.redact() once per block; the same
        # value must keep its index across those calls.
        result1 = redactor.redact("Alice meets Bob", [
            span(0, 5, "private_person"),
            span(12, 15, "private_person"),
        ])
        assert "[PESSOA_01]" in result1.redacted_text  # Alice
        assert "[PESSOA_02]" in result1.redacted_text  # Bob

        result2 = redactor.redact("Bob calls Alice", [
            span(0, 3, "private_person"),
            span(10, 15, "private_person"),
        ])
        # Bob still 02, Alice still 01 — counters preserved.
        assert result2.redacted_text == "[PESSOA_02] calls [PESSOA_01]"

    def test_reset_counters_isolates_documents(self, redactor: Redactor) -> None:
        redactor.redact("Alice", [span(0, 5, "private_person")])
        redactor.reset_counters()
        result = redactor.redact("Bob", [span(0, 3, "private_person")])
        # Counter restarted at 1
        assert result.redacted_text == "[PESSOA_01]"

    def test_label_without_brackets_appends_index(self) -> None:
        # If label has no closing bracket, the index is appended directly.
        policy = make_policy({
            "private_email": {"strategy": "indexed", "label": "EMAIL"},
        })
        result = Redactor(policy).redact(
            "x@y.com", [span(0, 7, "private_email")]
        )
        assert result.redacted_text == "EMAIL_01"

    def test_two_digit_zero_pad(self) -> None:
        # Indices below 10 are zero-padded to two digits.
        policy = make_policy({
            "private_email": {"strategy": "indexed", "label": "[E]"},
        })
        red = Redactor(policy)
        text = " ".join(f"a{i}@x.com" for i in range(3))
        spans = []
        offset = 0
        for i in range(3):
            email = f"a{i}@x.com"
            spans.append(span(offset, offset + len(email), "private_email"))
            offset += len(email) + 1
        result = red.redact(text, spans)
        assert result.redacted_text == "[E_01] [E_02] [E_03]"


# ---------------------------------------------------------------------------
# 4. Specific strategies — exercised via inline custom policies
# ---------------------------------------------------------------------------

def test_replace_strategy_uses_fixed_label() -> None:
    policy = make_policy({
        "private_email": {"strategy": "replace", "label": "[EMAIL]"},
    })
    result = Redactor(policy).redact(
        "Contact user@example.com",
        [span(8, 24, "private_email")],
    )
    assert "[EMAIL]" in result.redacted_text
    assert result.applied_spans[0].strategy == "replace"


def test_pseudonym_strategy_is_stable() -> None:
    policy = make_policy({
        "private_person": {"strategy": "pseudonym", "label": "[PERSON]"},
    })
    a = Redactor(policy).redact("Hello Jane Doe, welcome.", [span(6, 14, "private_person")])
    b = Redactor(policy).redact("Jane Doe submitted.", [span(0, 8, "private_person")])
    assert a.applied_spans[0].replacement == b.applied_spans[0].replacement


def test_mask_strategy_replaces_each_char() -> None:
    policy = make_policy({
        "private_phone": {
            "strategy": "mask", "label": "[PHONE]", "mask_char": "*"
        },
    })
    result = Redactor(policy).redact(
        "Phone: 555-0199",
        [span(7, 15, "private_phone")],
    )
    replacement = result.applied_spans[0].replacement
    assert set(replacement) == {"*"}
    assert len(replacement) == len("555-0199")


def test_suppress_strategy_removes_content() -> None:
    policy = make_policy({
        "secret": {"strategy": "suppress", "label": ""},
    })
    result = Redactor(policy).redact(
        "Password: hunter2 is weak",
        [span(10, 17, "secret")],
    )
    assert "hunter2" not in result.redacted_text
    assert result.applied_spans[0].replacement == ""


# ---------------------------------------------------------------------------
# 5. Edge cases
# ---------------------------------------------------------------------------

def test_unknown_entity_type_skipped(redactor: Redactor) -> None:
    text = "Token abc123xyz is confidential"
    spans = [span(6, 15, "unknown_entity")]
    result = redactor.redact(text, spans)
    assert result.redacted_text == text
    assert result.applied_spans == []
    assert result.stats == {}


def test_no_pii_returns_original(redactor: Redactor) -> None:
    text = "The quick brown fox jumps over the lazy dog."
    result = redactor.redact(text, [])
    assert result.redacted_text == text
    assert result.applied_spans == []
    assert result.stats == {}


def test_multiple_spans(redactor: Redactor) -> None:
    text = "Name: Carol Baskin  Email: carol@fake.org"
    spans = [
        span(6, 18, "private_person"),
        span(27, 41, "private_email"),
    ]
    result = redactor.redact(text, spans)
    assert "Carol Baskin" not in result.redacted_text
    assert "carol@fake.org" not in result.redacted_text
    assert "[PESSOA_01]" in result.redacted_text
    assert "[EMAIL_01]" in result.redacted_text
    assert result.stats == {"private_person": 1, "private_email": 1}
