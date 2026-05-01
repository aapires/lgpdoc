"""Detection rules for secrets that must never reach a downstream consumer."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from . import RuleMatch

RuleCtor = Callable[..., "RuleMatch"]


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# JWT: three base64url-safe segments separated by dots.
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_\-]{8,}\."
    r"eyJ[A-Za-z0-9_\-]{8,}\."
    r"[A-Za-z0-9_\-]{8,}\b"
)

# "Bearer <opaque-token>" — common in HTTP Authorization headers.
_BEARER_RE = re.compile(
    r"(?i)bearer\s+([A-Za-z0-9._\-]{20,})"
)

# PEM-encoded private key blocks (RSA, EC, OPENSSH, DSA, generic).
_PEM_KEY_RE = re.compile(
    r"-----BEGIN(?:\s+(?:RSA|EC|OPENSSH|DSA|ENCRYPTED))?\s+PRIVATE\s+KEY-----"
    r"[\s\S]+?"
    r"-----END(?:\s+(?:RSA|EC|OPENSSH|DSA|ENCRYPTED))?\s+PRIVATE\s+KEY-----"
)

# Common API key formats. Each alternative is anchored with word boundaries.
_API_KEY_RE = re.compile(
    r"\b("
    r"sk-[A-Za-z0-9_\-]{20,}"            # OpenAI / generic
    r"|ghp_[A-Za-z0-9]{30,}"              # GitHub personal token
    r"|gho_[A-Za-z0-9]{30,}"              # GitHub OAuth
    r"|github_pat_[A-Za-z0-9_]{40,}"      # GitHub fine-grained
    r"|AKIA[0-9A-Z]{16}"                  # AWS access key
    r"|ASIA[0-9A-Z]{16}"                  # AWS temporary key
    r"|xox[abprs]-[A-Za-z0-9\-]{20,}"     # Slack
    r"|AIza[0-9A-Za-z_\-]{35}"            # Google API
    r")\b"
)


# ---------------------------------------------------------------------------
# Rule functions
# ---------------------------------------------------------------------------

def find_jwts(text: str, match_cls: RuleCtor) -> list["RuleMatch"]:
    return [
        match_cls(rule_id="jwt", start=m.start(), end=m.end(), severity="critical")
        for m in _JWT_RE.finditer(text)
    ]


def find_bearer_tokens(text: str, match_cls: RuleCtor) -> list["RuleMatch"]:
    return [
        match_cls(rule_id="bearer_token", start=m.start(), end=m.end(), severity="critical")
        for m in _BEARER_RE.finditer(text)
    ]


def find_pem_keys(text: str, match_cls: RuleCtor) -> list["RuleMatch"]:
    return [
        match_cls(rule_id="private_key", start=m.start(), end=m.end(), severity="critical")
        for m in _PEM_KEY_RE.finditer(text)
    ]


def find_api_keys(text: str, match_cls: RuleCtor) -> list["RuleMatch"]:
    return [
        match_cls(rule_id="api_key", start=m.start(), end=m.end(), severity="critical")
        for m in _API_KEY_RE.finditer(text)
    ]
