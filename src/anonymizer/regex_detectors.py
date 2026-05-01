"""Deterministic regex detectors for Brazilian identifiers and other PII.

Each function takes raw text and returns a list of ``DetectedSpan`` tagged
with a stable ``entity_type``. They are wired into the augmented client
when their kind is enabled in the runtime configuration.

Patterns deliberately favour **precision over recall**: most require a
contextual keyword (RG, CNH, Passaporte, etc.) so we don't bleed into
unrelated digit sequences. CPFs/CNPJs use check-digit validation
(see ``augmentations.detect_cpfs`` / ``detect_cnpjs``).
"""
from __future__ import annotations

import hashlib
import re

from .models import DetectedSpan


def _hash(s: str) -> str:
    return hashlib.sha256(" ".join(s.lower().split()).encode("utf-8")).hexdigest()


def _make(
    entity_type: str,
    source: str,
    *,
    confidence: float = 0.95,
):
    """Helper to build a ``DetectedSpan`` factory bound to a given kind."""
    def _build(start: int, end: int, text: str) -> DetectedSpan:
        return DetectedSpan(
            start=start,
            end=end,
            entity_type=entity_type,
            confidence=confidence,
            text_hash=_hash(text),
            source=source,
        )
    return _build


# ---------------------------------------------------------------------------
# Identity documents (require a labelling keyword to avoid false positives)
# ---------------------------------------------------------------------------

# RG — varies wildly by state. Require explicit "RG" keyword nearby.
_RG_RE = re.compile(
    r"(?i)\bRG\s*[:°.]?\s*(\d{1,2}\.?\d{3}\.?\d{3}-?[\dXx])\b"
)

# CNH — 11 digits next to a CNH keyword
_CNH_RE = re.compile(
    r"(?i)\bCNH\s*[:°.]?\s*(\d{11})\b"
)

# Passaporte BR — 2 letters + 6 digits, with keyword
_PASSAPORTE_RE = re.compile(
    r"(?i)\b(?:passaporte|passport)\s*[:°.]?\s*([A-Z]{2}\d{6})\b"
)

# Título de Eleitor — 12 digits, optionally spaced; keyword required
_TITULO_RE = re.compile(
    r"(?i)\b(?:t[íi]tulo(?:\s+de\s+eleitor)?|eleitor[al]?)\s*[:°.]?\s*"
    r"(\d{4}\s?\d{4}\s?\d{4})\b"
)

# PIS / NIS / PASEP / NIT — 11 digits
_PIS_RE = re.compile(
    r"(?i)\b(?:PIS|NIS|PASEP|NIT)\s*[:°.]?\s*(\d{3}\.?\d{5}\.?\d{2}-?\d)\b"
)

# CTPS — variable, with keyword
_CTPS_RE = re.compile(
    r"(?i)\bCTPS\s*(?:n[º°.]?\s*)?(\d{4,8}(?:[\s\-/]\d{1,5})?)\b"
)

# Cartão SUS — 15 digits with keyword
_SUS_RE = re.compile(
    r"(?i)\b(?:SUS|CNS|cart[ãa]o\s+(?:do\s+)?SUS|cart[ãa]o\s+nacional)\s*"
    r"[:°.]?\s*(\d{15})\b"
)


# ---------------------------------------------------------------------------
# Professional IDs — UF + number; the keyword is built into the format
# ---------------------------------------------------------------------------

_OAB_RE = re.compile(r"(?i)\bOAB(?:[/\s\-]*[A-Z]{2})?[/\s\-]*\d{1,6}\b")
_CRM_RE = re.compile(r"(?i)\bCRM(?:[/\s\-]*[A-Z]{2})?[/\s\-]*\d{3,6}\b")
_CREA_RE = re.compile(r"(?i)\bCREA(?:[/\s\-]*[A-Z]{2})?[/\s\-]*\d{4,7}\b")


# ---------------------------------------------------------------------------
# Vehicle data
# ---------------------------------------------------------------------------

# Placa — old (ABC-1234) and Mercosul (ABC1D23) formats
_PLACA_RE = re.compile(r"\b[A-Z]{3}[\s\-]?[0-9][A-Z0-9][0-9]{2}\b")

_RENAVAM_RE = re.compile(r"(?i)\bRENAVAM\s*[:°.]?\s*(\d{9,11})\b")


# ---------------------------------------------------------------------------
# Legal / fiscal
# ---------------------------------------------------------------------------

# Processo judicial CNJ: NNNNNNN-DD.AAAA.J.TR.OOOO
_CNJ_RE = re.compile(
    r"\b\d{7}-?\d{2}\.?\d{4}\.?\d\.?\d{2}\.?\d{4}\b"
)

_IE_RE = re.compile(
    r"(?i)\b(?:I\.?E\.?|inscri[çc][ãa]o\s+estadual)\s*[:°.]?\s*"
    r"(\d[\d./\-\s]{5,17}\d)\b"
)


# ---------------------------------------------------------------------------
# Network / location
# ---------------------------------------------------------------------------

# IPv4 with octet range validation
_IP_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)

# CEP brasileiro: 5+3 digits with optional dash. Already in verification rules,
# now re-exposed for redaction-stage detection.
_CEP_RE = re.compile(r"\b\d{5}-?\d{3}\b")


# ---------------------------------------------------------------------------
# Financial
# ---------------------------------------------------------------------------

# Money amounts in BRL
_FINANCEIRO_RE = re.compile(
    r"R\$\s*\d{1,3}(?:\.\d{3})*(?:,\d{2})?"
)


# ---------------------------------------------------------------------------
# Detector functions
# ---------------------------------------------------------------------------

def _detect(
    text: str,
    pattern: re.Pattern[str],
    entity_type: str,
    source: str,
) -> list[DetectedSpan]:
    """Generic detector — uses the full match span as the redaction range."""
    build = _make(entity_type, source)
    return [
        build(m.start(), m.end(), m.group())
        for m in pattern.finditer(text)
    ]


def detect_rg(text: str) -> list[DetectedSpan]:
    return _detect(text, _RG_RE, "rg", "br_rg")


def detect_cnh(text: str) -> list[DetectedSpan]:
    return _detect(text, _CNH_RE, "cnh", "br_cnh")


def detect_passaporte(text: str) -> list[DetectedSpan]:
    return _detect(text, _PASSAPORTE_RE, "passaporte", "br_passaporte")


def detect_titulo_eleitor(text: str) -> list[DetectedSpan]:
    return _detect(text, _TITULO_RE, "titulo_eleitor", "br_titulo")


def detect_pis(text: str) -> list[DetectedSpan]:
    return _detect(text, _PIS_RE, "pis", "br_pis")


def detect_ctps(text: str) -> list[DetectedSpan]:
    return _detect(text, _CTPS_RE, "ctps", "br_ctps")


def detect_sus(text: str) -> list[DetectedSpan]:
    return _detect(text, _SUS_RE, "sus", "br_sus")


def detect_oab(text: str) -> list[DetectedSpan]:
    return _detect(text, _OAB_RE, "oab", "br_oab")


def detect_crm(text: str) -> list[DetectedSpan]:
    return _detect(text, _CRM_RE, "crm", "br_crm")


def detect_crea(text: str) -> list[DetectedSpan]:
    return _detect(text, _CREA_RE, "crea", "br_crea")


def detect_placa(text: str) -> list[DetectedSpan]:
    return _detect(text, _PLACA_RE, "placa", "br_placa")


def detect_renavam(text: str) -> list[DetectedSpan]:
    return _detect(text, _RENAVAM_RE, "renavam", "br_renavam")


def detect_processo_cnj(text: str) -> list[DetectedSpan]:
    return _detect(text, _CNJ_RE, "processo_cnj", "br_cnj")


def detect_inscricao_estadual(text: str) -> list[DetectedSpan]:
    return _detect(text, _IE_RE, "inscricao_estadual", "br_ie")


def detect_ip(text: str) -> list[DetectedSpan]:
    return _detect(text, _IP_RE, "ip", "ipv4")


def detect_cep(text: str) -> list[DetectedSpan]:
    return _detect(text, _CEP_RE, "cep", "br_cep")


def detect_financeiro(text: str) -> list[DetectedSpan]:
    return _detect(text, _FINANCEIRO_RE, "financeiro", "brl_amount")


# ---------------------------------------------------------------------------
# Legal entities — companies and government bodies (entity_type
# ``private_company``, marker label ``[PESSOA_JUR]``).
#
# Three patterns, all keyword-anchored to keep precision high:
#   1. Brazilian corporate suffix (Ltda, S.A., EIRELI, ME, EPP)
#   2. Government body keyword (Ministério, Secretaria, Tribunal, ...)
#   3. Education institution keyword (Universidade, Faculdade, ...)
#
# Names without any of these anchors (e.g. a bare ``"Acme"`` mention)
# are deliberately NOT matched — too risky for false positives. The
# reviewer can mark such cases manually via the UI.
# ---------------------------------------------------------------------------

# Brazilian corporate suffixes (post-fix to the company name).
_COMPANY_SUFFIX = (
    r"Ltda\.?|"
    r"S\.?\s?[Aa]\.?|"          # S.A., S A, S.a
    r"S/A|"
    r"EIRELI|"
    r"EPP|"
    r"M\.?E\.?"                  # ME or M.E.
)

# A capitalised Portuguese-friendly token. ``\w`` covers latin chars
# under Python's default Unicode flag.
_COMPANY_NAME_TOKEN = r"[A-ZÀ-Ý][\w'&-]*"
# Connectors allowed inside the name body. Wrapped in (?:...) so the
# ``|`` alternation doesn't bleed into the surrounding ``\s+`` when the
# constant is interpolated into the larger pattern.
_COMPANY_NAME_CONNECTOR = r"(?:d[aoe]s?|de|da|do|e)"

# Match: 1..6 capitalised tokens (with optional connectors between)
# followed by a corporate suffix. No trailing ``\b`` — suffixes can end
# in a literal dot (``Ltda.``, ``S.A.``) and ``\b`` doesn't fire
# between two non-word characters.
_COMPANY_RE = re.compile(
    r"\b("
    rf"{_COMPANY_NAME_TOKEN}"
    rf"(?:\s+(?:{_COMPANY_NAME_CONNECTOR}\s+)?{_COMPANY_NAME_TOKEN}){{0,5}}"
    r"\s+"
    rf"(?:{_COMPANY_SUFFIX})"
    r")",
    re.UNICODE,
)


# Government body keywords. Each must be the first token of the match.
_GOV_KEYWORD = (
    r"Minist[ée]rio|"
    r"Secretaria|Subsecretaria|"
    r"Departamento|"
    r"Tribunal|"
    r"Procuradoria(?:-Geral)?|"
    r"Defensoria|"
    r"C[âa]mara|"
    r"Assembleia|"
    r"Prefeitura|"
    r"Governo|"
    r"Receita|"
    r"Pol[íi]cia|"
    r"Justi[çc]a|"
    r"Conselho|"
    r"Comiss[ãa]o|"
    r"Ag[êe]ncia|"
    r"Autarquia|"
    r"Funda[çc][ãa]o|"
    r"Diretoria|"
    r"Superintend[êe]ncia|"
    r"Casa Civil"
)
_GOV_QUALIFIER = (
    r"d[aoe]s?|de|da|do|e|para|"
    r"[Nn]acional|[Ff]ederal|[Ee]stadual|[Mm]unicipal|"
    r"P[úu]blico|P[úu]blica|Geral|Civil|Militar"
)

# After the keyword, accept 1..8 tokens that are either capitalised
# words or connectors / qualifiers. Greedy match — the trailing tokens
# stop at the first non-capitalised, non-qualifier token (e.g. lowercase
# verb, punctuation).
_GOV_BODY_RE = re.compile(
    rf"\b(?:{_GOV_KEYWORD})"
    rf"(?:\s+(?:{_COMPANY_NAME_TOKEN}|{_GOV_QUALIFIER})){{1,8}}",
    re.UNICODE,
)


# Education institution keywords.
_EDU_KEYWORD = (
    r"Universidade|"
    r"Faculdade|"
    r"Instituto|"
    r"Escola|"
    r"Col[ée]gio|"
    r"Centro Universit[áa]rio"
)

_EDU_RE = re.compile(
    rf"\b(?:{_EDU_KEYWORD})"
    rf"(?:\s+(?:{_COMPANY_NAME_TOKEN}|{_GOV_QUALIFIER})){{1,8}}",
    re.UNICODE,
)


def detect_company_with_suffix(text: str) -> list[DetectedSpan]:
    """Companies ending in Ltda / S.A. / EIRELI / EPP / ME."""
    return _detect(text, _COMPANY_RE, "private_company", "br_company_suffix")


def detect_government_body(text: str) -> list[DetectedSpan]:
    """Government bodies that start with a known keyword
    (Ministério, Secretaria, Tribunal, ...)."""
    return _detect(text, _GOV_BODY_RE, "private_company", "br_gov_body")


def detect_education_institution(text: str) -> list[DetectedSpan]:
    """Universities, schools, institutes that start with a known
    keyword (Universidade, Faculdade, Instituto, ...)."""
    return _detect(text, _EDU_RE, "private_company", "br_edu_institution")


# ---------------------------------------------------------------------------
# Brazilian dates (entity_type ``private_date``)
#
# Five patterns, all anchored on date-shaped tokens. Each requires
# enough structure (day + month + year, or written month name with year)
# so a bare ``5/12`` or a fraction ``2/3`` doesn't trigger.
#
# Day / month / year sub-patterns enforce real ranges so ``32/12/2024``
# or ``15/13/2024`` won't match — cuts false positives on serial-number
# strings that happen to look like dates.
# ---------------------------------------------------------------------------

_DATE_DAY = r"(?:0?[1-9]|[12]\d|3[01])"          # 1-31
_DATE_MONTH = r"(?:0?[1-9]|1[0-2])"              # 1-12
_DATE_YEAR = r"(?:\d{4}|\d{2})"                  # 2- or 4-digit year

_DATE_MONTHS_PT = (
    r"(?:janeiro|fevereiro|mar[çc]o|abril|maio|junho|"
    r"julho|agosto|setembro|outubro|novembro|dezembro)"
)
_DATE_MONTHS_ABBR_PT = (
    r"(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\.?"
)

# Numeric: 25/12/2024, 25-12-2024, 25.12.2024, 5/12/24, etc.
_DATE_NUMERIC_RE = re.compile(
    rf"\b{_DATE_DAY}[/.\-]{_DATE_MONTH}[/.\-]{_DATE_YEAR}\b"
)

# ISO: 2024-12-25
_DATE_ISO_RE = re.compile(rf"\b\d{{4}}-{_DATE_MONTH}-{_DATE_DAY}\b")

# Full textual: "25 de dezembro de 2024" (year optional)
_DATE_FULL_TEXT_RE = re.compile(
    rf"\b{_DATE_DAY}\s+de\s+{_DATE_MONTHS_PT}(?:\s+de\s+\d{{4}})?\b",
    re.IGNORECASE | re.UNICODE,
)

# Month + year: "dezembro de 2024" (no day)
_DATE_MONTH_YEAR_RE = re.compile(
    rf"\b{_DATE_MONTHS_PT}\s+de\s+\d{{4}}\b",
    re.IGNORECASE | re.UNICODE,
)

# Abbreviated mixed: "25 dez 2024", "25/dez/2024", "25 dez. 2024"
_DATE_ABBR_RE = re.compile(
    rf"\b{_DATE_DAY}[\s/.\-]+{_DATE_MONTHS_ABBR_PT}[\s/.\-]+\d{{2,4}}\b",
    re.IGNORECASE | re.UNICODE,
)


def detect_dates(text: str) -> list[DetectedSpan]:
    """Detect Brazilian date formats.

    Covered:
        * Numeric with day+month+year — ``25/12/2024``, ``25.12.24``,
          ``25-12-2024``, etc. Validates day≤31 and month≤12 to cut
          false positives on serial-shaped digits.
        * ISO ``yyyy-mm-dd``.
        * Full textual ``25 de dezembro de 2024`` (year optional).
        * Month+year textual ``dezembro de 2024``.
        * Abbreviated ``25 dez 2024``, ``25/dez/2024``.

    NOT covered (too risky for false positives):
        * Day+month only (``5/12``, ``25 de dezembro`` without year
          context).
        * Numeric ``mm/yyyy`` (ambiguous with versions / fractions).

    Multiple patterns are run; longer matches consume shorter overlapping
    matches so ``25 de dezembro de 2024`` doesn't also produce a
    redundant ``dezembro de 2024`` span.
    """
    build = _make("private_date", "br_date")
    spans: list[DetectedSpan] = []
    kept_ranges: list[tuple[int, int]] = []
    # Order matters: longest patterns first so shorter ones can be
    # discarded as contained.
    for pattern in (
        _DATE_FULL_TEXT_RE,
        _DATE_ABBR_RE,
        _DATE_MONTH_YEAR_RE,
        _DATE_ISO_RE,
        _DATE_NUMERIC_RE,
    ):
        for m in pattern.finditer(text):
            start, end = m.start(), m.end()
            # Drop if fully contained in a previously kept span.
            if any(s <= start and end <= e for s, e in kept_ranges):
                continue
            kept_ranges.append((start, end))
            spans.append(build(start, end, m.group()))
    spans.sort(key=lambda s: (s.start, -(s.end - s.start)))
    return spans


# ---------------------------------------------------------------------------
# Registry — maps detector kind to its function. Used by the augmented client.
# ---------------------------------------------------------------------------

REGEX_DETECTORS: dict[str, callable] = {
    "rg": detect_rg,
    "cnh": detect_cnh,
    "passaporte": detect_passaporte,
    "titulo_eleitor": detect_titulo_eleitor,
    "pis": detect_pis,
    "ctps": detect_ctps,
    "sus": detect_sus,
    "oab": detect_oab,
    "crm": detect_crm,
    "crea": detect_crea,
    "placa": detect_placa,
    "renavam": detect_renavam,
    "processo_cnj": detect_processo_cnj,
    "inscricao_estadual": detect_inscricao_estadual,
    "ip": detect_ip,
    "cep": detect_cep,
    "financeiro": detect_financeiro,
    # Three keyword-anchored detectors for legal entities — share the
    # ``private_company`` entity type but each emits a distinct
    # detection_source so the diagnostic UI can show which one fired.
    "private_company__suffix": detect_company_with_suffix,
    "private_company__gov": detect_government_body,
    "private_company__edu": detect_education_institution,
    # Brazilian date formats — complements OPF, which struggles with
    # locale-specific variants like "25 de dezembro de 2024".
    "private_date": detect_dates,
}
