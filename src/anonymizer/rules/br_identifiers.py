"""Brazilian PII rules: email, phone, CPF, CNPJ, CEP.

CPF and CNPJ matches are validated using their official check-digit algorithm
to drastically reduce false positives.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from . import RuleMatch  # circular import only at type-check time

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# BR phone — DDD + 8 or 9 digits, with optional country code +55 and separators.
_BR_PHONE_RE = re.compile(
    r"(?:\+?55[\s\-]?)?"          # optional +55 country code
    r"\(?\d{2}\)?[\s\-]?"          # area code (DDD)
    r"9?\d{4}[\s\-]?\d{4}"         # 8 or 9 digit number
)

_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_CNPJ_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_CEP_RE = re.compile(r"\b\d{5}-\d{3}\b")


# ---------------------------------------------------------------------------
# CPF / CNPJ check-digit validators
# ---------------------------------------------------------------------------

def _digits(text: str) -> str:
    return "".join(c for c in text if c.isdigit())


def _validate_cpf(digits: str) -> bool:
    if len(digits) != 11 or len(set(digits)) == 1:
        return False

    def check(prefix: str, factor: int) -> int:
        total = sum(int(c) * (factor - i) for i, c in enumerate(prefix))
        rem = (total * 10) % 11
        return rem if rem < 10 else 0

    return (
        check(digits[:9], 10) == int(digits[9])
        and check(digits[:10], 11) == int(digits[10])
    )


def _validate_cnpj(digits: str) -> bool:
    if len(digits) != 14 or len(set(digits)) == 1:
        return False

    weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    weights2 = [6] + weights1

    def check(prefix: str, weights: list[int]) -> int:
        total = sum(int(c) * w for c, w in zip(prefix, weights))
        rem = total % 11
        return 0 if rem < 2 else 11 - rem

    return (
        check(digits[:12], weights1) == int(digits[12])
        and check(digits[:13], weights2) == int(digits[13])
    )


# ---------------------------------------------------------------------------
# Rule functions
# ---------------------------------------------------------------------------

# Each rule takes text and a RuleMatch constructor (passed in to avoid a
# circular import with rules/__init__.py).
RuleCtor = Callable[..., "RuleMatch"]


def find_emails(text: str, match_cls: RuleCtor) -> list["RuleMatch"]:
    return [
        match_cls(rule_id="email", start=m.start(), end=m.end(), severity="medium")
        for m in _EMAIL_RE.finditer(text)
    ]


def find_br_phones(text: str, match_cls: RuleCtor) -> list["RuleMatch"]:
    matches: list["RuleMatch"] = []
    for m in _BR_PHONE_RE.finditer(text):
        # Reject candidates with too few digits (e.g. trailing 4-digit numbers).
        if len(_digits(m.group())) < 10:
            continue
        matches.append(
            match_cls(rule_id="br_phone", start=m.start(), end=m.end(), severity="medium")
        )
    return matches


def find_cpfs(text: str, match_cls: RuleCtor) -> list["RuleMatch"]:
    matches: list["RuleMatch"] = []
    for m in _CPF_RE.finditer(text):
        if _validate_cpf(_digits(m.group())):
            matches.append(
                match_cls(rule_id="cpf", start=m.start(), end=m.end(), severity="high")
            )
    return matches


def find_cnpjs(text: str, match_cls: RuleCtor) -> list["RuleMatch"]:
    matches: list["RuleMatch"] = []
    for m in _CNPJ_RE.finditer(text):
        if _validate_cnpj(_digits(m.group())):
            matches.append(
                match_cls(rule_id="cnpj", start=m.start(), end=m.end(), severity="high")
            )
    return matches


def find_ceps(text: str, match_cls: RuleCtor) -> list["RuleMatch"]:
    return [
        match_cls(rule_id="cep", start=m.start(), end=m.end(), severity="low")
        for m in _CEP_RE.finditer(text)
    ]
