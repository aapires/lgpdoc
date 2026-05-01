"""Deterministic regex/algorithmic rules for the verification stage.

Each rule function takes a string and returns a list of ``RuleMatch``.
``ALL_RULES`` is the canonical registry consumed by ``Verifier``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import br_identifiers as br
from . import secrets as sec


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    start: int
    end: int
    severity: str = "medium"  # "low" | "medium" | "high" | "critical"


# Each rule is a callable: (text, RuleMatch ctor) -> list[RuleMatch]
RuleFn = Callable[[str], list["RuleMatch"]]


def _make_runner(module, fn_name: str) -> RuleFn:
    return lambda text: getattr(module, fn_name)(text, RuleMatch)


ALL_RULES: list[RuleFn] = [
    _make_runner(br, "find_emails"),
    _make_runner(br, "find_br_phones"),
    _make_runner(br, "find_cpfs"),
    _make_runner(br, "find_cnpjs"),
    _make_runner(br, "find_ceps"),
    _make_runner(sec, "find_jwts"),
    _make_runner(sec, "find_bearer_tokens"),
    _make_runner(sec, "find_pem_keys"),
    _make_runner(sec, "find_api_keys"),
]


def run_all_rules(text: str) -> list[RuleMatch]:
    """Run every registered rule on *text* and return concatenated matches."""
    matches: list[RuleMatch] = []
    for rule in ALL_RULES:
        matches.extend(rule(text))
    return matches


__all__ = ["RuleMatch", "RuleFn", "ALL_RULES", "run_all_rules"]
