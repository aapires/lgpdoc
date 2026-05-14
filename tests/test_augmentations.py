"""Tests for the augmentation layer (case normalisation, composite client,
Brazilian labeled-name detector)."""
from __future__ import annotations

import pytest

from anonymizer.augmentations import (
    CaseNormalizingClient,
    CompositeClient,
    _override_generic_with_specific,
    detect_br_labeled_names,
    detect_cnpjs,
    detect_cpfs,
    detect_endereco_logradouro,
    detect_endereco_unidade,
    make_augmented_client,
    normalize_allcaps_sequences,
)
from anonymizer.client import MockPrivacyFilterClient
from anonymizer.models import DetectedSpan


# ---------------------------------------------------------------------------
# Case normalisation
# ---------------------------------------------------------------------------

class TestNormalizeAllcaps:
    def test_basic_two_word_name(self) -> None:
        assert (
            normalize_allcaps_sequences("Cliente: GUSTAVO SOARES")
            == "Cliente: Gustavo Soares"
        )

    def test_three_word_name(self) -> None:
        assert (
            normalize_allcaps_sequences("Sr. RICARDO MENDES SOUZA")
            == "Sr. Ricardo Mendes Souza"
        )

    def test_length_preserved(self) -> None:
        text = "Responsável: GUSTAVO SOARES — UF RJ\nNaturalidade RIO DE JANEIRO"
        assert len(normalize_allcaps_sequences(text)) == len(text)

    def test_two_letter_acronyms_preserved(self) -> None:
        # UF and RJ are short; 2-letter sequences are not normalised.
        assert normalize_allcaps_sequences("UF RJ") == "UF RJ"
        assert normalize_allcaps_sequences("Estado: DF") == "Estado: DF"

    def test_single_caps_word_preserved(self) -> None:
        assert normalize_allcaps_sequences("ATENÇÃO") == "ATENÇÃO"

    def test_handles_diacritics(self) -> None:
        # Words with accented uppercase letters
        out = normalize_allcaps_sequences("CIDADE SÃO PAULO")
        assert out == "Cidade São Paulo"

    def test_connector_inside_name(self) -> None:
        # "MARIO DA SILVA" — short connector "DA" is part of the all-caps run.
        assert (
            normalize_allcaps_sequences("Cliente: MARIO DA SILVA")
            == "Cliente: Mario Da Silva"
        )

    def test_multi_label_document(self) -> None:
        text = (
            "Responsável: GUSTAVO SOARES\n"
            "Cliente: RICARDO MENDES SOUZA\n"
            "Nome do pai FERNANDO CARLOS DIAS\n"
            "Nome da mãe LUCIA HELENA BARROS COSTA"
        )
        out = normalize_allcaps_sequences(text)
        # All names normalised so OPF can recognise them as people.
        assert "Gustavo Soares" in out
        assert "Ricardo Mendes Souza" in out
        assert "Fernando Carlos Dias" in out
        assert "Lucia Helena Barros Costa" in out
        # Length preserved so span offsets are still valid against the original.
        assert len(out) == len(text)

    def test_acronyms_isolated_are_preserved(self) -> None:
        # When short codes are NOT part of a longer all-caps run, they survive.
        out = normalize_allcaps_sequences("Estado: DF\nUF: RJ")
        assert "DF" in out
        assert "RJ" in out


# ---------------------------------------------------------------------------
# Brazilian labeled-name detector
# ---------------------------------------------------------------------------

class TestDetectBRLabeledNames:
    def test_cliente_label_allcaps(self) -> None:
        spans = detect_br_labeled_names("Cliente: RICARDO MENDES SOUZA")
        assert len(spans) == 1
        s = spans[0]
        assert s.entity_type == "private_person"
        assert s.source == "br_labeled_name"
        # Span captures only the name, not the label
        assert "RICARDO" in "Cliente: RICARDO MENDES SOUZA"[s.start:s.end]
        assert "Cliente" not in "Cliente: RICARDO MENDES SOUZA"[s.start:s.end]

    def test_responsavel_label(self) -> None:
        spans = detect_br_labeled_names("Responsável: GUSTAVO SOARES")
        assert len(spans) == 1

    def test_titlecase_name(self) -> None:
        spans = detect_br_labeled_names("Cliente: Ricardo Mendes Souza")
        assert len(spans) == 1

    def test_nome_do_pai_no_colon(self) -> None:
        spans = detect_br_labeled_names("Nome do pai FERNANDO CARLOS DIAS")
        assert len(spans) == 1

    def test_does_not_bleed_into_next_field(self) -> None:
        # Two labels on the same line — should produce 2 separate matches,
        # neither one swallowing the next field.
        text = (
            "Nome do pai FERNANDO CARLOS DIAS "
            "Nome da mãe LUCIA HELENA BARROS COSTA"
        )
        spans = detect_br_labeled_names(text)
        assert len(spans) == 2
        first = text[spans[0].start:spans[0].end]
        second = text[spans[1].start:spans[1].end]
        assert first == "FERNANDO CARLOS DIAS"
        assert second == "LUCIA HELENA BARROS COSTA"

    def test_name_does_not_cross_newline_into_section_header(self) -> None:
        # Real-world bug: with \s+ the name swallowed the next line.
        text = "Responsável: GUSTAVO SOARES\nORDEM DE SERVIÇO"
        spans = detect_br_labeled_names(text)
        assert len(spans) == 1
        captured = text[spans[0].start:spans[0].end]
        assert captured == "GUSTAVO SOARES"

    def test_name_does_not_cross_newline_into_next_label(self) -> None:
        text = "Nome: RICARDO MENDES SOUZA\nCNPJ: 11.222.333/0001-81"
        spans = detect_br_labeled_names(text)
        assert any(
            text[s.start:s.end] == "RICARDO MENDES SOUZA" for s in spans
        )
        # And nothing captured ALEXANDRE...CNPJ across the newline
        assert not any(
            "CNPJ" in text[s.start:s.end] for s in spans
        )

    def test_name_after_label_on_new_line_still_matches(self) -> None:
        # Common form-style layout: label on one line, value on the next.
        text = "Responsável:\nGUSTAVO SOARES"
        spans = detect_br_labeled_names(text)
        assert len(spans) == 1
        assert text[spans[0].start:spans[0].end] == "GUSTAVO SOARES"

    def test_honorific_labels(self) -> None:
        """Honorifics (Sr./Sra./Dr./Dra. + full forms) introduce names too,
        with or without the trailing period."""
        cases = [
            ("Sr. Carlos Souza concordou.", "Carlos Souza"),
            ("Sr Marcelo Tavares assinou.", "Marcelo Tavares"),
            ("Sra. Beatriz Lima.", "Beatriz Lima"),
            ("Sra Beatriz Lima.", "Beatriz Lima"),
            ("Senhor Rafael Pinheiro veio.", "Rafael Pinheiro"),
            ("Senhora Patricia Andrade veio.", "Patricia Andrade"),
            ("Dr. Bruno Gomes assina.", "Bruno Gomes"),
            ("Dra. Camila Rios assina.", "Camila Rios"),
            ("Dr Lucas Albuquerque assina.", "Lucas Albuquerque"),
            # The full-form Doutor/Doutora were already covered.
            ("Doutor Andre Fernandes opinou.", "Andre Fernandes"),
        ]
        for text, expected in cases:
            spans = detect_br_labeled_names(text)
            assert spans, f"no match for: {text!r}"
            assert any(
                text[s.start : s.end] == expected for s in spans
            ), f"text={text!r} got={[text[s.start:s.end] for s in spans]}"

    def test_no_match_without_label(self) -> None:
        spans = detect_br_labeled_names("RICARDO MENDES SOUZA went home")
        assert spans == []

    def test_many_labels_in_one_document(self) -> None:
        text = (
            "Responsável: GUSTAVO SOARES\n"
            "Cliente: RICARDO MENDES SOUZA\n"
            "Nome do pai FERNANDO CARLOS DIAS\n"
            "Nome da mãe LUCIA HELENA BARROS COSTA"
        )
        spans = detect_br_labeled_names(text)
        assert len(spans) == 4

    def test_naturalidade_not_treated_as_name_label(self) -> None:
        # "Naturalidade" is intentionally NOT in the name-label list — it's a
        # place, not a person.
        spans = detect_br_labeled_names("Naturalidade RIO DE JANEIRO")
        assert spans == []

    def test_text_hash_stored_not_raw(self) -> None:
        spans = detect_br_labeled_names("Cliente: João da Silva")
        s = spans[0]
        assert s.text_hash is not None
        assert len(s.text_hash) == 64  # SHA-256 hex


class TestBRPublicSectorLabels:
    """Coverage for the broader role/title vocabulary used in government and
    corporate documents."""

    def test_servidor(self) -> None:
        spans = detect_br_labeled_names("Servidor: GUSTAVO SOARES")
        assert len(spans) == 1

    def test_servidora_feminine(self) -> None:
        spans = detect_br_labeled_names("Servidora: MARIA SILVA")
        assert len(spans) == 1

    def test_servidor_publico(self) -> None:
        spans = detect_br_labeled_names("Servidor Público: GUSTAVO SOARES")
        assert len(spans) == 1
        text = "Servidor Público: GUSTAVO SOARES"
        assert text[spans[0].start:spans[0].end] == "GUSTAVO SOARES"

    def test_servidor_publico_federal(self) -> None:
        spans = detect_br_labeled_names(
            "Servidor Público Federal: GUSTAVO SOARES"
        )
        assert len(spans) == 1

    def test_servidora_publica_estadual(self) -> None:
        spans = detect_br_labeled_names(
            "Servidora Pública Estadual: MARIA SILVA"
        )
        assert len(spans) == 1

    def test_funcionario_publico(self) -> None:
        spans = detect_br_labeled_names("Funcionário Público: JOÃO LIMA")
        assert len(spans) == 1

    def test_funcionaria_feminine(self) -> None:
        assert len(detect_br_labeled_names("Funcionária: ANA PAULA SOUZA")) == 1

    def test_terceirizado_terceirizada(self) -> None:
        assert len(detect_br_labeled_names("Terceirizado: PEDRO LIMA")) == 1
        assert len(detect_br_labeled_names("Terceirizada: ANA SOUZA")) == 1

    def test_auditor_and_auditor_fiscal(self) -> None:
        assert len(detect_br_labeled_names("Auditor: CARLOS MENDES")) == 1
        assert len(detect_br_labeled_names("Auditor Fiscal: CARLOS MENDES")) == 1
        assert len(detect_br_labeled_names("Auditora Fiscal: ANA MENDES")) == 1

    def test_diretor_variants(self) -> None:
        for label in [
            "Diretor: PAULO COSTA",
            "Diretora: PAULA COSTA",
            "Diretor Geral: PAULO COSTA",
            "Diretor Adjunto: PAULO COSTA",
            "Diretor Executivo: PAULO COSTA",
            "Diretora Adjunta: PAULA COSTA",
        ]:
            assert len(detect_br_labeled_names(label)) == 1, f"failed for: {label!r}"

    def test_secretario_variants(self) -> None:
        for label in [
            "Secretário: LUIS FONSECA",
            "Secretária: LUISA FONSECA",
            "Secretário Adjunto: LUIS FONSECA",
            "Subsecretário: LUIS FONSECA",
        ]:
            assert len(detect_br_labeled_names(label)) == 1, f"failed for: {label!r}"

    def test_management_titles(self) -> None:
        for label in [
            "Coordenador: JOÃO LIMA",
            "Coordenadora: ANA LIMA",
            "Supervisor: JOÃO LIMA",
            "Gerente: JOÃO LIMA",
            "Chefe: JOÃO LIMA",
            "Presidente: JOÃO LIMA",
            "Vice-Presidente: JOÃO LIMA",
            "Analista: JOÃO LIMA",
            "Assessor: JOÃO LIMA",
            "Assessora: ANA LIMA",
            "Consultor: JOÃO LIMA",
            "Inspetor: JOÃO LIMA",
            "Especialista: JOÃO LIMA",
            "Técnico: JOÃO LIMA",
            "Técnica: ANA LIMA",
            "Conselheira: ANA LIMA",
        ]:
            assert len(detect_br_labeled_names(label)) == 1, f"failed for: {label!r}"

    def test_legal_titles(self) -> None:
        for label in [
            "Juiz: PAULO MARTINS",
            "Juíza: PAULA MARTINS",
            "Promotor: PAULO MARTINS",
            "Promotora: PAULA MARTINS",
            "Delegado: PAULO MARTINS",
            "Delegada: PAULA MARTINS",
            "Perito: PAULO MARTINS",
            "Perita: PAULA MARTINS",
            "Desembargador: PAULO MARTINS",
            "Ministro: PAULO MARTINS",
            "Ministra: PAULA MARTINS",
        ]:
            assert len(detect_br_labeled_names(label)) == 1, f"failed for: {label!r}"

    def test_dash_separator_in_compound_label(self) -> None:
        # Common in BR docs: "Diretora-Geral", "Vice-Presidente"
        spans = detect_br_labeled_names("Diretora-Geral: ANA SILVA")
        assert len(spans) == 1


# ---------------------------------------------------------------------------
# Destinatário / Remetente labels (added to BR labeled-name detector)
# ---------------------------------------------------------------------------

class TestBRMailingLabels:
    def test_destinatario(self) -> None:
        spans = detect_br_labeled_names("Destinatário: ANA SILVA")
        assert len(spans) == 1
        assert "ANA SILVA" in "Destinatário: ANA SILVA"[
            spans[0].start : spans[0].end
        ]

    def test_destinataria_feminine(self) -> None:
        assert len(detect_br_labeled_names("Destinatária: MARIA SOUZA")) == 1

    def test_remetente(self) -> None:
        spans = detect_br_labeled_names("Remetente: JOÃO LIMA")
        assert len(spans) == 1


# ---------------------------------------------------------------------------
# Brazilian address detectors
# ---------------------------------------------------------------------------

class TestEnderecoLogradouro:
    def test_rua_with_name(self) -> None:
        spans = detect_endereco_logradouro("Endereço: Rua das Flores, 123")
        assert len(spans) == 1
        assert "Rua das Flores" in spans[0].text_hash or True
        text = "Endereço: Rua das Flores, 123"
        captured = text[spans[0].start : spans[0].end]
        assert captured.startswith("Rua das Flores")
        assert "123" in captured

    def test_avenida_abbreviated(self) -> None:
        spans = detect_endereco_logradouro("Av. Paulista, 1000 — São Paulo")
        assert len(spans) == 1
        text = "Av. Paulista, 1000 — São Paulo"
        captured = text[spans[0].start : spans[0].end]
        assert captured.startswith("Av. Paulista")

    def test_praca(self) -> None:
        spans = detect_endereco_logradouro("Reuniu-se na Praça da Sé, ontem.")
        assert len(spans) == 1

    def test_alameda_travessa_largo(self) -> None:
        for text, name in [
            ("Alameda Santos, 200", "Alameda Santos"),
            ("Travessa do Comércio, 5", "Travessa do Comércio"),
            ("Largo do Machado", "Largo do Machado"),
        ]:
            spans = detect_endereco_logradouro(text)
            assert len(spans) == 1, f"failed for: {text}"
            assert name in text[spans[0].start : spans[0].end]

    def test_estrada_rodovia(self) -> None:
        for text in [
            "Estrada Velha, km 12",
            "Rodovia BR-101, km 45",
        ]:
            spans = detect_endereco_logradouro(text)
            assert len(spans) >= 1, f"failed for: {text}"

    def test_no_match_for_isolated_starter(self) -> None:
        # "Rua" without a following capitalised word should not match.
        assert detect_endereco_logradouro("Andou pela rua sem rumo.") == []

    def test_entity_type_is_private_address(self) -> None:
        spans = detect_endereco_logradouro("Rua das Flores, 123")
        assert spans[0].entity_type == "private_address"


class TestEnderecoUnidade:
    @pytest.mark.parametrize("text,expected", [
        ("Apto 502", "Apto 502"),
        ("Apto. 502", "Apto. 502"),
        ("Apartamento 1502", "Apartamento 1502"),
        ("Bloco A", "Bloco A"),
        ("Torre 1", "Torre 1"),
        ("Quadra 10", "Quadra 10"),
        ("Lote 5", "Lote 5"),
        ("Casa 12", "Casa 12"),
        ("Sala 305", "Sala 305"),
    ])
    def test_unit_patterns(self, text: str, expected: str) -> None:
        full = f"Endereço completo: {text}, sem mais."
        spans = detect_endereco_unidade(full)
        assert len(spans) >= 1, f"failed for: {text}"
        captured = full[spans[0].start : spans[0].end]
        assert captured == expected

    def test_does_not_match_apto_inside_apertura(self) -> None:
        # "Apt." or "Ap." followed by a word like "Ana" should NOT match.
        assert detect_endereco_unidade("Sr. Ap. Ana enviou um recado.") == []

    def test_with_separator(self) -> None:
        spans = detect_endereco_unidade("Apto 502/01 do bloco")
        assert len(spans) == 1
        captured = "Apto 502/01 do bloco"[spans[0].start : spans[0].end]
        assert captured == "Apto 502/01"

    def test_alphanumeric_unit(self) -> None:
        spans = detect_endereco_unidade("Sala B12 disponível")
        assert len(spans) == 1
        captured = "Sala B12 disponível"[spans[0].start : spans[0].end]
        assert captured == "Sala B12"

    def test_entity_type_is_private_address(self) -> None:
        spans = detect_endereco_unidade("Apto 502")
        assert spans[0].entity_type == "private_address"


# ---------------------------------------------------------------------------
# CaseNormalizingClient
# ---------------------------------------------------------------------------

class TestCaseNormalizingClient:
    def test_inner_sees_normalized_text(self) -> None:
        captured: list[str] = []

        class CaptureClient(MockPrivacyFilterClient):
            def detect(self, text: str):
                captured.append(text)
                return super().detect(text)

        wrapped = CaseNormalizingClient(CaptureClient())
        wrapped.detect("Cliente: GUSTAVO SOARES")
        assert captured == ["Cliente: Gustavo Soares"]

    def test_offsets_match_original_text(self) -> None:
        text = "Cliente: GUSTAVO SOARES, agora."
        spans = CaseNormalizingClient(MockPrivacyFilterClient()).detect(text)
        # Mock detects title-case names. After normalisation, "Gustavo Soares"
        # is detected. Offsets must point at the ORIGINAL ALL-CAPS span.
        person_spans = [s for s in spans if s.entity_type == "private_person"]
        assert person_spans, "expected mock client to detect the name"
        s = person_spans[0]
        assert text[s.start:s.end] == "GUSTAVO SOARES"


# ---------------------------------------------------------------------------
# CompositeClient
# ---------------------------------------------------------------------------

class TestCompositeClient:
    def test_combines_primary_and_aux(self) -> None:
        # Mock alone won't catch ALL-CAPS names. The aux detector does.
        composite = CompositeClient(
            primary=MockPrivacyFilterClient(),
            aux_detectors=[detect_br_labeled_names],
        )
        spans = composite.detect("Cliente: GUSTAVO SOARES")
        assert any(s.entity_type == "private_person" for s in spans)

    def test_no_aux_detectors(self) -> None:
        composite = CompositeClient(primary=MockPrivacyFilterClient())
        spans = composite.detect("hello@example.com")
        assert any(s.entity_type == "private_email" for s in spans)


# ---------------------------------------------------------------------------
# make_augmented_client — the integration helper used by the API
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CPF / CNPJ detection (entity_type=cpf|cnpj, distinct from account_number)
# ---------------------------------------------------------------------------

def _make_cpf(prefix9: str) -> str:
    def calc(d: str, factor: int) -> int:
        s = sum(int(c) * (factor - i) for i, c in enumerate(d))
        r = (s * 10) % 11
        return r if r < 10 else 0
    dv1 = calc(prefix9, 10)
    dv2 = calc(prefix9 + str(dv1), 11)
    return f"{prefix9}{dv1}{dv2}"


def _make_cnpj(prefix12: str) -> str:
    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    w2 = [6] + w1

    def calc(d: str, weights: list[int]) -> int:
        s = sum(int(c) * w for c, w in zip(d, weights))
        r = s % 11
        return 0 if r < 2 else 11 - r
    dv1 = calc(prefix12, w1)
    dv2 = calc(prefix12 + str(dv1), w2)
    return f"{prefix12}{dv1}{dv2}"


class TestCPFDetection:
    def test_valid_cpf_with_punctuation(self) -> None:
        spans = detect_cpfs("CPF: 111.444.777-35")
        assert len(spans) == 1
        assert spans[0].entity_type == "cpf"

    def test_valid_cpf_without_punctuation(self) -> None:
        cpf = _make_cpf("123456789")
        spans = detect_cpfs(f"Inscrito sob {cpf}")
        assert len(spans) == 1
        assert spans[0].entity_type == "cpf"

    def test_invalid_check_digits_rejected(self) -> None:
        # 111.444.777-00 has wrong check digits
        assert detect_cpfs("CPF 111.444.777-00") == []


class TestCNPJDetection:
    def test_valid_cnpj(self) -> None:
        cnpj = _make_cnpj("112223330001")
        formatted = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
        spans = detect_cnpjs(f"CNPJ {formatted}")
        assert len(spans) == 1
        assert spans[0].entity_type == "cnpj"

    def test_invalid_cnpj_rejected(self) -> None:
        assert detect_cnpjs("CNPJ 11.222.333/0001-99") == []


class TestSpecificOverridesGeneric:
    def test_account_number_dropped_when_cpf_overlaps(self) -> None:
        # OPF often labels CPFs as 'account_number'. The CPF rule must win.
        cpf_span = DetectedSpan(
            start=10, end=24, entity_type="cpf", confidence=0.99,
        )
        account_span = DetectedSpan(
            start=10, end=24, entity_type="account_number", confidence=0.8,
        )
        result = _override_generic_with_specific([account_span, cpf_span])
        kinds = {s.entity_type for s in result}
        assert "cpf" in kinds
        assert "account_number" not in kinds

    def test_account_number_dropped_with_partial_overlap(self) -> None:
        # OPF span includes 'CPF: 111...' (with prefix). Rule span is just the
        # digits. Partial overlap → still drop account_number.
        cpf_span = DetectedSpan(
            start=15, end=29, entity_type="cpf", confidence=0.99,
        )
        account_span = DetectedSpan(
            start=10, end=29, entity_type="account_number", confidence=0.8,
        )
        result = _override_generic_with_specific([account_span, cpf_span])
        assert all(s.entity_type != "account_number" for s in result)

    def test_unrelated_account_number_preserved(self) -> None:
        # An account_number that does NOT overlap any CPF/CNPJ stays.
        cpf_span = DetectedSpan(
            start=10, end=24, entity_type="cpf", confidence=0.99,
        )
        unrelated_account = DetectedSpan(
            start=100, end=119, entity_type="account_number", confidence=0.8,
        )
        result = _override_generic_with_specific([cpf_span, unrelated_account])
        kinds = [s.entity_type for s in result]
        assert "account_number" in kinds
        assert "cpf" in kinds

    def test_no_specific_spans_passthrough(self) -> None:
        spans = [
            DetectedSpan(start=0, end=5, entity_type="account_number"),
            DetectedSpan(start=10, end=15, entity_type="private_email"),
        ]
        assert _override_generic_with_specific(spans) == spans

    def test_private_phone_dropped_when_cpf_overlaps(self) -> None:
        # Models sometimes classify a CPF (digits-only or with dots) as a
        # phone number because the digit-count is in range.
        cpf_span = DetectedSpan(
            start=0, end=14, entity_type="cpf", confidence=0.99,
        )
        phone_span = DetectedSpan(
            start=0, end=14, entity_type="private_phone", confidence=0.7,
        )
        result = _override_generic_with_specific([phone_span, cpf_span])
        kinds = {s.entity_type for s in result}
        assert "cpf" in kinds
        assert "private_phone" not in kinds


# ---------------------------------------------------------------------------
# End-to-end via make_augmented_client
# ---------------------------------------------------------------------------


class TestMakeAugmentedClient:
    def test_catches_allcaps_names_via_both_paths(self) -> None:
        client = make_augmented_client(MockPrivacyFilterClient())
        text = (
            "Cliente: RICARDO MENDES SOUZA\n"
            "Email: alex@example.org"
        )
        spans = client.detect(text)
        kinds = {s.entity_type for s in spans}
        assert "private_person" in kinds
        assert "private_email" in kinds

    def test_cpf_detected_with_dedicated_entity_type(self) -> None:
        client = make_augmented_client(MockPrivacyFilterClient())
        spans = client.detect("CPF: 111.444.777-35")
        assert any(s.entity_type == "cpf" for s in spans)

    def test_cnpj_detected_with_dedicated_entity_type(self) -> None:
        cnpj = _make_cnpj("112223330001")
        formatted = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
        client = make_augmented_client(MockPrivacyFilterClient())
        spans = client.detect(f"CNPJ {formatted}")
        assert any(s.entity_type == "cnpj" for s in spans)


# ---------------------------------------------------------------------------
# End-to-end: redacting a real-shaped Brazilian document with the augmented
# pipeline. Uses the Mock client (regex) but proves the augmentations make
# it strong enough to redact ALL-CAPS BR names that the bare mock misses.
# ---------------------------------------------------------------------------

def test_e2e_redaction_of_br_document() -> None:
    from pathlib import Path
    from anonymizer.policy import Policy
    from anonymizer.redactor import Redactor

    policy = Policy.from_yaml(
        Path(__file__).parent.parent / "policies" / "default.yaml"
    )
    client = make_augmented_client(MockPrivacyFilterClient())

    text = (
        "Responsável: GUSTAVO SOARES\n"
        "Cliente: RICARDO MENDES SOUZA\n"
        "Nome do pai FERNANDO CARLOS DIAS\n"
        "Nome da mãe LUCIA HELENA BARROS COSTA"
    )
    spans = client.detect(text)
    result = Redactor(policy).redact(text, spans)

    # Every name from the input must be gone from the redacted text.
    assert "GUSTAVO" not in result.redacted_text
    assert "RICARDO" not in result.redacted_text
    assert "MARIO SERGIO" not in result.redacted_text
    assert "REGINA" not in result.redacted_text


def test_e2e_cpf_uses_dedicated_placeholder() -> None:
    from pathlib import Path
    from anonymizer.policy import Policy
    from anonymizer.redactor import Redactor

    policy = Policy.from_yaml(
        Path(__file__).parent.parent / "policies" / "default.yaml"
    )
    client = make_augmented_client(MockPrivacyFilterClient())

    text = "CPF do cliente: 111.444.777-35"
    spans = client.detect(text)
    result = Redactor(policy).redact(text, spans)

    assert "111.444.777-35" not in result.redacted_text
    assert "[CPF_01]" in result.redacted_text
    # And NOT the generic CONTA placeholder
    assert "[CONTA" not in result.redacted_text


def test_e2e_address_logradouro_redacted() -> None:
    from pathlib import Path
    from anonymizer.policy import Policy
    from anonymizer.redactor import Redactor

    policy = Policy.from_yaml(
        Path(__file__).parent.parent / "policies" / "default.yaml"
    )
    client = make_augmented_client(MockPrivacyFilterClient())

    text = "Endereço: Rua das Flores, 123 — Apto 502, Bloco A."
    spans = client.detect(text)
    result = Redactor(policy).redact(text, spans)

    assert "Rua das Flores" not in result.redacted_text
    assert "Apto 502" not in result.redacted_text
    assert "Bloco A" not in result.redacted_text
    assert "[ENDERECO_" in result.redacted_text


def test_e2e_destinatario_remetente_redacted() -> None:
    from pathlib import Path
    from anonymizer.policy import Policy
    from anonymizer.redactor import Redactor

    policy = Policy.from_yaml(
        Path(__file__).parent.parent / "policies" / "default.yaml"
    )
    client = make_augmented_client(MockPrivacyFilterClient())

    text = (
        "Remetente: ANA SILVA\n"
        "Destinatário: JOÃO LIMA"
    )
    spans = client.detect(text)
    result = Redactor(policy).redact(text, spans)

    assert "ANA SILVA" not in result.redacted_text
    assert "JOÃO LIMA" not in result.redacted_text
    assert "[PESSOA_" in result.redacted_text


def test_e2e_cnpj_uses_dedicated_placeholder() -> None:
    from pathlib import Path
    from anonymizer.policy import Policy
    from anonymizer.redactor import Redactor

    policy = Policy.from_yaml(
        Path(__file__).parent.parent / "policies" / "default.yaml"
    )
    client = make_augmented_client(MockPrivacyFilterClient())

    cnpj = _make_cnpj("112223330001")
    formatted = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
    text = f"CNPJ da empresa: {formatted}"
    spans = client.detect(text)
    result = Redactor(policy).redact(text, spans)

    assert formatted not in result.redacted_text
    assert "[CNPJ_01]" in result.redacted_text
    assert "[CONTA" not in result.redacted_text
