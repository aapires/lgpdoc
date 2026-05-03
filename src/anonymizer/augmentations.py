"""Detection augmentations to improve PII recall on Brazilian documents.

The OpenAI Privacy Filter is trained primarily on English / natural-case
text. Brazilian documents commonly:
  - store names in ALL CAPS ("ALEXANDRE ANDRADE PIRES")
  - prefix names with localised labels ("Cliente:", "Nome do pai", ...)

These augmentations layer on top of any base detector. Two strategies:

1. ``CaseNormalizingClient`` — title-cases ALL-CAPS sequences before
   handing the text to the wrapped detector. Length is preserved exactly,
   so returned span offsets index into the original text directly.

2. ``detect_br_labeled_names`` — deterministic regex that recognises names
   following common Brazilian labels (Cliente, Responsável, Nome do pai,
   etc.). Acts as a safety net even when OPF misses the name.

3. ``CompositeClient`` — runs a primary detector + N auxiliary detectors,
   merging their spans. The Redactor's overlap resolution deduplicates
   spans pointing at the same region.
"""
from __future__ import annotations

import hashlib
import re
from typing import Callable

from .client import PrivacyFilterClient
from .models import DetectedSpan
from .rules.br_identifiers import (
    _CNPJ_RE,
    _CPF_RE,
    _digits,
    _validate_cnpj,
    _validate_cpf,
)


# ---------------------------------------------------------------------------
# Case normalisation — length-preserving title-casing of ALL-CAPS sequences.
# ---------------------------------------------------------------------------

_ALLCAPS = "A-ZÀÁÂÃÄÅÉÊËÍÎÏÓÔÕÖÚÛÜÇÑ"

# A sequence of ALL-CAPS words: the first word must be at least 3 chars
# (rules out 2-letter codes like RJ, DF, UF), but subsequent words may be
# shorter (so we still catch connectors like "DE"/"DA"/"DO" inside names).
_ALLCAPS_SEQUENCE = re.compile(
    rf"[{_ALLCAPS}]{{3,}}(?:\s+[{_ALLCAPS}]+)+"
)
_ALLCAPS_WORD_IN_SEQ = re.compile(rf"[{_ALLCAPS}]+")


def normalize_allcaps_sequences(text: str) -> str:
    """Lower the case of every ALL-CAPS word that is part of a 2+ word run.

    Standalone all-caps words (acronyms like RJ, DF, UF) are preserved.
    Length is preserved exactly — every character keeps its position.

    Examples
    --------
    "Cliente: GUSTAVO SOARES — UF RJ"
        → "Cliente: Gustavo Soares — UF RJ"
    "Naturalidade RIO DE JANEIRO"
        → "Naturalidade Rio De Janeiro"
    """
    def title_case(m: re.Match) -> str:
        w = m.group()
        return w[0] + w[1:].lower() if len(w) > 1 else w

    return _ALLCAPS_SEQUENCE.sub(
        lambda m: _ALLCAPS_WORD_IN_SEQ.sub(title_case, m.group()),
        text,
    )


class CaseNormalizingClient(PrivacyFilterClient):
    """Wraps any ``PrivacyFilterClient``. Title-cases ALL-CAPS sequences
    before detection so case-sensitive models recognise them as proper
    nouns. Span offsets returned by the inner client index into the
    *original* text directly because normalisation is length-preserving.
    """

    def __init__(self, inner: PrivacyFilterClient) -> None:
        self._inner = inner

    def detect(self, text: str) -> list[DetectedSpan]:
        return self._inner.detect(normalize_allcaps_sequences(text))


# ---------------------------------------------------------------------------
# Composite client — primary detector + auxiliary regex detectors.
# ---------------------------------------------------------------------------

AuxDetector = Callable[[str], list[DetectedSpan]]


class CompositeClient(PrivacyFilterClient):
    """Combines a primary detector with deterministic auxiliary detectors."""

    def __init__(
        self,
        primary: PrivacyFilterClient,
        aux_detectors: list[AuxDetector] | None = None,
    ) -> None:
        self._primary = primary
        self._aux = list(aux_detectors or [])

    def detect(self, text: str) -> list[DetectedSpan]:
        spans = list(self._primary.detect(text))
        for detector in self._aux:
            spans.extend(detector(text))
        return spans


# ---------------------------------------------------------------------------
# Brazilian labeled-name detector.
# ---------------------------------------------------------------------------

# Common labels that introduce a person's name in BR documents.
# Multi-word labels and feminine variants are listed explicitly. The optional
# QUALIFIER suffix below handles compound titles like "Servidor Público
# Federal", "Auditor Fiscal", "Diretor Adjunto" etc. without enumerating
# every combination.
_BR_BASE_LABEL = (
    # ------------------------------------------------------------------
    # Family / identity
    # ------------------------------------------------------------------
    r"nome do pai|nome da m[ãa]e|nome completo|"
    r"filia[çc][ãa]o|c[ôo]njuge|"

    # ------------------------------------------------------------------
    # Contractual / legal parties
    # ------------------------------------------------------------------
    r"cliente|signat[áa]rio|signat[áa]ria|"
    r"contratante|contratado|contratada|"
    r"procurador|procuradora|"
    r"outorgante|outorgado|outorgada|"
    r"requerente|requerido|requerida|"
    r"testemunha|representante|"
    r"comprador|compradora|vendedor|vendedora|"
    r"locador|locadora|locat[áa]rio|locat[áa]ria|"
    r"solicitante|"
    r"benefici[áa]rio|benefici[áa]ria|"
    r"devedor|devedora|credor|credora|"
    r"destinat[áa]rio|destinat[áa]ria|remetente|"

    # ------------------------------------------------------------------
    # Public sector / employment
    # ------------------------------------------------------------------
    r"respons[áa]vel|"
    r"servidor|servidora|"
    r"funcion[áa]rio|funcion[áa]ria|"
    r"terceirizado|terceirizada|"
    r"colaborador|colaboradora|"
    r"empregado|empregada|"
    r"estagi[áa]rio|estagi[áa]ria|"

    # ------------------------------------------------------------------
    # Management / hierarchy
    # ------------------------------------------------------------------
    r"auditor|auditora|"
    r"diretor|diretora|"
    r"secret[áa]rio|secret[áa]ria|"
    r"subsecret[áa]rio|subsecret[áa]ria|"
    r"coordenador|coordenadora|"
    r"supervisor|supervisora|"
    r"vice-presidente|presidente|"
    r"gerente|chefe|"
    r"analista|"
    r"assessor|assessora|"
    r"consultor|consultora|"
    r"inspetor|inspetora|"
    r"especialista|"
    r"t[ée]cnico|t[ée]cnica|"
    r"conselheiro|conselheira|"

    # ------------------------------------------------------------------
    # Legal / law enforcement / judiciary
    # ------------------------------------------------------------------
    r"fiscal|"
    r"promotor|promotora|"
    r"delegado|delegada|"
    r"perito|perita|"
    r"ju[íi]za|juiz|"
    r"desembargador|desembargadora|"
    r"ministro|ministra|"
    r"advogado|advogada|"

    # ------------------------------------------------------------------
    # Education / health
    # ------------------------------------------------------------------
    r"professor|professora|"
    r"m[ée]dico|m[ée]dica|"
    r"enfermeiro|enfermeira|"
    r"doutor|doutora|"

    # ------------------------------------------------------------------
    # Honorifics (period optional — "Sr." and "Sr Joao Silva" both work)
    # ------------------------------------------------------------------
    r"senhor|senhora|sr\.?|sra\.?|dr\.?|dra\.?|"

    # ------------------------------------------------------------------
    # Generic fallback (must come last — most generic match)
    # ------------------------------------------------------------------
    r"nome"
)

# Qualifier words that may follow the main label, in 0..3 occurrences.
# Examples: "Servidor Público Federal", "Auditor Fiscal", "Diretor Adjunto",
# "Secretário Executivo", "Diretora-Geral".
_BR_LABEL_QUALIFIER = (
    r"p[úu]blic[oa]|federal|estadual|municipal|"
    r"fiscal|adjunt[oa]|geral|executiv[oa]|"
    r"substitut[oa]|titular|presidente"
)

_BR_NAME_LABEL = (
    rf"(?:{_BR_BASE_LABEL})"
    rf"(?:[\s-]+(?:{_BR_LABEL_QUALIFIER})){{0,3}}"
)

# Tokens that signal the next field is starting — used as a negative
# lookahead so the name body doesn't bleed into the next field.
_STOP_TOKEN = (
    r"Nome|Endere[çc]o|Cidade|Estado|Telefone|"
    r"E-mail|CEP|CPF|CNPJ|"
    r"Naturalidade|UF|Unidade|Filia[çc][ãa]o|Cliente|Respons[áa]vel|"
    r"Data"
)

_NAME_TOKEN = r"[A-ZÀ-Ý][A-ZÀ-Ýa-zà-ý'-]+"
_NAME_CONN = r"(?:de|da|do|dos|das|e)"

# Body of a name: 1 + (1..4) tokens, joined by space/tab and optional
# connector. Crucially, separators here are ``[ \t]+`` (NOT ``\s+``) so the
# match never crosses a line break — a name on one line cannot bleed into
# tokens on the next, even if those tokens look name-shaped.
_NAME_BODY = (
    rf"{_NAME_TOKEN}"
    rf"(?:[ \t]+(?:{_NAME_CONN}[ \t]+)?(?!(?:{_STOP_TOKEN})\b){_NAME_TOKEN}){{1,4}}"
)

_BR_LABELED_NAME_RE = re.compile(
    rf"\b(?i:{_BR_NAME_LABEL})[\s:]+({_NAME_BODY})",
)


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def detect_br_labeled_names(text: str) -> list[DetectedSpan]:
    """Find names that follow common Brazilian document labels."""
    spans: list[DetectedSpan] = []
    for m in _BR_LABELED_NAME_RE.finditer(text):
        name = m.group(1)
        spans.append(
            DetectedSpan(
                start=m.start(1),
                end=m.end(1),
                entity_type="private_person",
                confidence=0.9,
                text_hash=_hash(name),
                source="br_labeled_name",
            )
        )
    return spans


def detect_cpfs(text: str) -> list[DetectedSpan]:
    """Detect Brazilian CPFs with validated check digits.

    Returns spans tagged as ``cpf`` (not ``account_number``) so that the
    redaction policy can substitute them with a CPF-specific placeholder.
    """
    spans: list[DetectedSpan] = []
    for m in _CPF_RE.finditer(text):
        if _validate_cpf(_digits(m.group())):
            spans.append(
                DetectedSpan(
                    start=m.start(),
                    end=m.end(),
                    entity_type="cpf",
                    confidence=0.99,
                    text_hash=_hash(m.group()),
                    source="br_cpf",
                )
            )
    return spans


def detect_cnpjs(text: str) -> list[DetectedSpan]:
    """Detect Brazilian CNPJs with validated check digits."""
    spans: list[DetectedSpan] = []
    for m in _CNPJ_RE.finditer(text):
        if _validate_cnpj(_digits(m.group())):
            spans.append(
                DetectedSpan(
                    start=m.start(),
                    end=m.end(),
                    entity_type="cnpj",
                    confidence=0.99,
                    text_hash=_hash(m.group()),
                    source="br_cnpj",
                )
            )
    return spans


# ---------------------------------------------------------------------------
# Brazilian address detectors
# ---------------------------------------------------------------------------

# Logradouro starters: street/avenue/etc. styles. Each may carry an optional
# trailing dot (Av., R., Estr., etc.).
_LOGRADOURO_STARTER = (
    r"Avenida|Av\.|"
    r"Rua|R\.|"
    r"Alameda|Al\.|"
    r"Travessa|Tv\.|"
    r"Pra[çc]a|"
    r"Largo|"
    r"Estrada|Estr\.|"
    r"Rodovia|Rod\.|"
    r"Caminho|"
    r"Servid[ãa]o|"
    r"Edif[íi]cio|Ed\.|"
    r"Conjunto|Cj\.?"
)

# After the starter, 1-6 tokens that are either capitalised words (incl.
# Portuguese diacritics) or short connectors (de, da, do, e, à, ao, etc.).
# An optional ", number" can follow (with house-number variations).
# NOTE: in regex alternation Python tries each option in order and stops at
# the first match (not the longest). So longer connectors must come first —
# "das" before "da", "dos" before "do".
_ADDR_CONNECTOR = r"(?:das|dos|aos|da|do|de|ao|à|e)"
_LOGRADOURO_RE = re.compile(
    rf"\b(?:{_LOGRADOURO_STARTER})\.?"
    r"\s+"
    rf"(?:[A-ZÀ-Ý][\wÀ-Ýà-ý'-]*|{_ADDR_CONNECTOR})"
    rf"(?:\s+(?:[A-ZÀ-Ý][\wÀ-Ýà-ý'-]*|{_ADDR_CONNECTOR})){{0,5}}"
    r"(?:\s*,?\s*(?:n[º°.]\s*)?\d+[A-Za-z]?(?:\s*[\-/]\s*\d+[A-Za-z]?)?)?",
    re.UNICODE,
)

# Unit identifiers: Apto 502, Bloco A, Torre 1, Quadra 10, Lote 5, etc.
_UNIDADE_STARTER = (
    r"Apartamento|Apto\.?|Apt\.?|Ap\.|"
    r"Bloco|Bl\.|"
    r"Torre|Tr\.|"
    r"Quadra|Qd\.|Q\.|"
    r"Lote|Lt\.|"
    r"Casa|"
    r"Sala"
)
# Body: digits (with optional letter suffix) OR a single uppercase letter
# optionally followed by digits. Negative lookahead `(?![a-zA-Z])` prevents
# matching "Ap. A" inside "Ap. Ana".
_UNIDADE_RE = re.compile(
    rf"\b(?:{_UNIDADE_STARTER})\.?\s*"
    r"(?:\d+[A-Za-z]?|[A-Z]\d*)"
    r"(?:\s*[\-/]\s*\d+[A-Za-z]?)?"
    r"(?![a-zA-Z])",
    re.UNICODE,
)


def detect_endereco_logradouro(text: str) -> list[DetectedSpan]:
    """Detect street-style addresses: 'Rua das Flores', 'Av. Paulista, 1000',
    'Praça da Sé', 'Travessa do Comércio'.

    Tagged as ``private_address`` so it overlaps cleanly with the OPF model's
    own address detections (the redactor's overlap resolution dedupes them).
    """
    spans: list[DetectedSpan] = []
    for m in _LOGRADOURO_RE.finditer(text):
        spans.append(
            DetectedSpan(
                start=m.start(),
                end=m.end(),
                entity_type="private_address",
                confidence=0.92,
                text_hash=_hash(m.group()),
                source="br_logradouro",
            )
        )
    return spans


def detect_endereco_unidade(text: str) -> list[DetectedSpan]:
    """Detect unit identifiers: 'Apto 502', 'Bloco A', 'Torre 1',
    'Quadra 10', 'Lote 5', 'Casa 12'."""
    spans: list[DetectedSpan] = []
    for m in _UNIDADE_RE.finditer(text):
        spans.append(
            DetectedSpan(
                start=m.start(),
                end=m.end(),
                entity_type="private_address",
                confidence=0.9,
                text_hash=_hash(m.group()),
                source="br_unidade",
            )
        )
    return spans


# ---------------------------------------------------------------------------
# Specific-over-generic override
# ---------------------------------------------------------------------------

def _override_generic_with_specific(
    spans: list[DetectedSpan],
) -> list[DetectedSpan]:
    """Drop any non-CPF/CNPJ span that overlaps a CPF or CNPJ match.

    Reasoning: CPF/CNPJ matches are validated against the official
    check-digit algorithm — a much stronger signal than the model's
    generic classification. Whenever such a rule fires on a region,
    every other span pointing at the same characters (account_number,
    private_phone, private_url, etc.) should yield so the resulting
    placeholder is the specific ``[CPF_NN]`` / ``[CNPJ_NN]``.
    """
    specific_ranges = [
        (s.start, s.end) for s in spans if s.entity_type in ("cpf", "cnpj")
    ]
    if not specific_ranges:
        return spans

    def overlaps_specific(span: DetectedSpan) -> bool:
        for rs, re_ in specific_ranges:
            if span.end > rs and span.start < re_:
                return True
        return False

    return [
        s
        for s in spans
        if s.entity_type in ("cpf", "cnpj") or not overlaps_specific(s)
    ]


# ---------------------------------------------------------------------------
# Convenience wrapper used by the API and CLI scripts.
# ---------------------------------------------------------------------------

class _OverridingComposite(CompositeClient):
    """CompositeClient that demotes ``account_number`` to CPF/CNPJ whenever
    a deterministic rule matched the same region, and filters out any span
    whose entity_type is not currently enabled in the runtime config.
    """

    def __init__(
        self,
        primary: PrivacyFilterClient,
        aux_detectors: list[AuxDetector] | None = None,
        get_enabled_kinds: Callable[[], set[str]] | None = None,
    ) -> None:
        super().__init__(primary, aux_detectors)
        self._get_enabled_kinds = get_enabled_kinds

    def detect(self, text: str) -> list[DetectedSpan]:
        spans = super().detect(text)
        spans = _override_generic_with_specific(spans)
        if self._get_enabled_kinds is None:
            return spans
        enabled = self._get_enabled_kinds()
        return [s for s in spans if s.entity_type in enabled]


def make_augmented_client(
    base: PrivacyFilterClient,
    *,
    get_enabled_kinds: Callable[[], set[str]] | None = None,
) -> PrivacyFilterClient:
    """Wrap *base* with the full augmentation stack.

    Layers:
      1. Case normalisation (helps with ALL-CAPS Brazilian docs)
      2. BR labeled-name detector (names after common labels)
      3. Validated CPF / CNPJ detection
      4. The full Brazilian regex detector registry (RG, CNH, OAB, CRM,
         CREA, Passaporte, Placa, IP, ...)

    If ``get_enabled_kinds`` is provided, every produced span is filtered
    against the returned set. This lets a settings page enable/disable
    individual entity types without rebuilding the client.
    """
    # Lazy import avoids a hard dep if the regex module ever grows costly.
    from .regex_detectors import REGEX_DETECTORS

    aux: list[AuxDetector] = [
        detect_br_labeled_names,
        detect_cpfs,
        detect_cnpjs,
        detect_endereco_logradouro,
        detect_endereco_unidade,
    ]
    aux.extend(REGEX_DETECTORS.values())

    return _OverridingComposite(
        primary=CaseNormalizingClient(base),
        aux_detectors=aux,
        get_enabled_kinds=get_enabled_kinds,
    )
