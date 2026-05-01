"""Value normalisers for container marker resolution.

Two detections refer to the same real entity (and therefore must share
the same marker within a container) when their normalised values are
equal. The function returned by :func:`get_normalizer` is the one piece
of code authorised to make that judgement — keep new entity types here
so the rule stays in one place.

Sprint 2 ships a conservative set of rules:

* CPF / CNPJ / phone-shaped → digits only.
* E-mail → trim + lowercase.
* Person / company → trim + lower + strip diacritics + collapse whitespace.
* Default → trim + lower + collapse whitespace.

Names/companies stay in the conservative band on purpose: the spec
explicitly disallows aggressive similarity-based merging in v1, because
that's how false-positive merges happen ("João da Silva" vs "Joao Silva
Filho" must NOT collapse without human review).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Callable


_DIGITS_RE = re.compile(r"\D+")
_WS_RE = re.compile(r"\s+")


def _digits_only(value: str) -> str:
    return _DIGITS_RE.sub("", value)


def _trim_lower(value: str) -> str:
    return value.strip().lower()


def _strip_diacritics(value: str) -> str:
    """Remove combining marks. Length and word boundaries are preserved
    by NFKD decomposition + ascii filter on combining chars only."""
    nfkd = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def _collapse_whitespace(value: str) -> str:
    return _WS_RE.sub(" ", value).strip()


# ---------------------------------------------------------------------------
# Normalisers per entity type
# ---------------------------------------------------------------------------

def normalize_document_number(value: str) -> str:
    """CPF/CNPJ/RG/CNH/etc. — strip every non-digit. ``""`` if the input
    has no digits at all (the resolver treats empty normalisations as
    ineligible for matching)."""
    return _digits_only(value)


def normalize_phone(value: str) -> str:
    return _digits_only(value)


def normalize_email(value: str) -> str:
    return _trim_lower(value)


def normalize_person(value: str) -> str:
    return _collapse_whitespace(_strip_diacritics(_trim_lower(value)))


def normalize_company(value: str) -> str:
    """Same rule as ``normalize_person``, kept as a separate function so
    Sprint 3+ can diverge (e.g. stripping ``Ltda``/``S.A.`` suffixes
    before matching) without renaming callers."""
    return _collapse_whitespace(_strip_diacritics(_trim_lower(value)))


def normalize_default(value: str) -> str:
    return _collapse_whitespace(_trim_lower(value))


# Mapping from internal entity type → normaliser. Keep in sync with the
# entity types produced by the augmented client (see ``settings_store``
# ``ALL_KINDS`` and the regex detector registry).
_NORMALIZERS: dict[str, Callable[[str], str]] = {
    # Identity documents — digits-only
    "cpf": normalize_document_number,
    "cnpj": normalize_document_number,
    "rg": normalize_document_number,
    "cnh": normalize_document_number,
    "passaporte": normalize_default,
    "titulo_eleitor": normalize_document_number,
    "pis": normalize_document_number,
    "ctps": normalize_document_number,
    "sus": normalize_document_number,
    # Professional registries — keep punctuation-insensitive
    "oab": normalize_default,
    "crm": normalize_default,
    "crea": normalize_default,
    # Vehicle data
    "placa": normalize_default,
    "renavam": normalize_document_number,
    # Legal / fiscal
    "processo_cnj": normalize_document_number,
    "inscricao_estadual": normalize_document_number,
    # PII categories from OPF + augmentations
    "private_person": normalize_person,
    "private_company": normalize_company,
    "private_email": normalize_email,
    "private_phone": normalize_phone,
    "private_address": normalize_default,
    "private_date": normalize_default,
    "private_url": normalize_default,
    "account_number": normalize_document_number,
    # Brazilian-specific
    "cep": normalize_document_number,
    "ip": normalize_default,
    "financeiro": normalize_default,
}


def get_normalizer(entity_type: str) -> Callable[[str], str]:
    """Return the normaliser for ``entity_type`` (defaults to
    ``normalize_default`` if the type is unknown)."""
    return _NORMALIZERS.get(entity_type, normalize_default)


def normalize(entity_type: str, value: str) -> str:
    """Convenience: ``get_normalizer(entity_type)(value)``."""
    return get_normalizer(entity_type)(value)
