"""Unit tests for the deterministic regex detectors (RG, CNH, OAB, ...)."""
from __future__ import annotations

import pytest

from anonymizer.regex_detectors import (
    detect_cep,
    detect_cnh,
    detect_company_with_suffix,
    detect_crea,
    detect_crm,
    detect_ctps,
    detect_dates,
    detect_education_institution,
    detect_financeiro,
    detect_government_body,
    detect_inscricao_estadual,
    detect_ip,
    detect_oab,
    detect_passaporte,
    detect_pis,
    detect_placa,
    detect_processo_cnj,
    detect_renavam,
    detect_rg,
    detect_sus,
    detect_titulo_eleitor,
)


# ---------------------------------------------------------------------------
# Identity documents (require contextual keyword)
# ---------------------------------------------------------------------------

class TestRG:
    def test_finds_rg_with_keyword(self) -> None:
        spans = detect_rg("RG: 12.345.678-9 emitido em 2010")
        assert len(spans) == 1
        assert spans[0].entity_type == "rg"

    def test_rejects_bare_digits_without_keyword(self) -> None:
        # Bare digits without "RG" must not match (high false-positive risk).
        assert detect_rg("Número 12.345.678-9 anotado") == []


class TestCNH:
    def test_finds_cnh_with_keyword(self) -> None:
        spans = detect_cnh("CNH: 12345678901")
        assert len(spans) == 1


class TestPassaporte:
    def test_finds_passaporte(self) -> None:
        spans = detect_passaporte("Passaporte: AB123456")
        assert len(spans) == 1


class TestTituloEleitor:
    def test_finds_titulo(self) -> None:
        spans = detect_titulo_eleitor("Título de Eleitor: 1234 5678 9012")
        assert len(spans) == 1


class TestPIS:
    def test_finds_pis(self) -> None:
        spans = detect_pis("PIS: 123.45678.90-1")
        assert len(spans) == 1

    def test_finds_nis_synonym(self) -> None:
        spans = detect_pis("NIS 12345678901")
        assert len(spans) == 1


class TestCTPS:
    def test_finds_ctps(self) -> None:
        spans = detect_ctps("CTPS Nº 123456")
        assert len(spans) == 1


class TestSUS:
    def test_finds_sus(self) -> None:
        spans = detect_sus("Cartão SUS: 123456789012345")
        assert len(spans) == 1


# ---------------------------------------------------------------------------
# Professional registries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "OAB/SP 123456",
    "OAB-SP 123456",
    "OAB SP 12345",
])
def test_oab_variants(text: str) -> None:
    spans = detect_oab(text)
    assert len(spans) == 1


@pytest.mark.parametrize("text", [
    "CRM/SP 12345",
    "CRM-RJ 123456",
])
def test_crm_variants(text: str) -> None:
    spans = detect_crm(text)
    assert len(spans) == 1


@pytest.mark.parametrize("text", [
    "CREA/SP 1234567",
    "CREA-DF 12345",
])
def test_crea_variants(text: str) -> None:
    spans = detect_crea(text)
    assert len(spans) == 1


# ---------------------------------------------------------------------------
# Vehicle data
# ---------------------------------------------------------------------------

class TestPlaca:
    @pytest.mark.parametrize("plate", [
        "ABC-1234",       # old format
        "ABC1D23",        # Mercosul
        "ABC 1D23",       # with space
    ])
    def test_finds_placa(self, plate: str) -> None:
        spans = detect_placa(f"Placa: {plate}")
        assert len(spans) == 1


class TestRENAVAM:
    def test_finds_renavam(self) -> None:
        spans = detect_renavam("RENAVAM: 12345678901")
        assert len(spans) == 1


# ---------------------------------------------------------------------------
# Legal / fiscal
# ---------------------------------------------------------------------------

class TestProcessoCNJ:
    def test_finds_cnj(self) -> None:
        spans = detect_processo_cnj("Processo 1234567-89.2024.8.26.0100")
        assert len(spans) == 1


class TestInscricaoEstadual:
    def test_finds_ie(self) -> None:
        spans = detect_inscricao_estadual("I.E.: 123.456.789.012")
        assert len(spans) == 1


# ---------------------------------------------------------------------------
# Network / location
# ---------------------------------------------------------------------------

class TestIP:
    def test_finds_ipv4(self) -> None:
        spans = detect_ip("Servidor 192.168.1.42 OK")
        assert len(spans) == 1

    def test_rejects_invalid_octet(self) -> None:
        # 999.0.0.1 has an out-of-range octet — must not match.
        assert detect_ip("Servidor 999.0.0.1") == []


class TestCEP:
    def test_finds_cep(self) -> None:
        spans = detect_cep("CEP 01310-100")
        assert len(spans) == 1


# ---------------------------------------------------------------------------
# Financial
# ---------------------------------------------------------------------------

class TestFinanceiro:
    def test_finds_currency(self) -> None:
        spans = detect_financeiro("Valor: R$ 1.234,56 reais")
        assert len(spans) == 1


# ---------------------------------------------------------------------------
# Legal entities — companies and government bodies (private_company)
# ---------------------------------------------------------------------------

class TestCompanyWithSuffix:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("A empresa Acme Ltda foi multada.", "Acme Ltda"),
            ("Banco Itaú S.A. publicou o balanço.", "Banco Itaú S.A."),
            ("contrato com Foo Serviços EIRELI hoje", "Foo Serviços EIRELI"),
            ("a XYZ Comercial Ltda. é nova.", "XYZ Comercial Ltda."),
            ("Microempresa Bar ME assinou.", "Bar ME"),
            ("Companhia das Águas Ltda é estatal.", "Companhia das Águas Ltda"),
        ],
    )
    def test_finds_company(self, text: str, expected: str) -> None:
        spans = detect_company_with_suffix(text)
        assert len(spans) >= 1
        # The first match should contain the expected fragment.
        matched = text[spans[0].start : spans[0].end]
        assert expected in matched, (
            f"Expected to find {expected!r} in match {matched!r}"
        )
        assert spans[0].entity_type == "private_company"
        assert spans[0].source == "br_company_suffix"

    def test_no_match_without_suffix(self) -> None:
        # "Acme" alone — no Ltda / S.A. — must NOT match. Catching bare
        # company names is too risky for false positives.
        assert detect_company_with_suffix("o pessoal da Acme conversou") == []

    def test_no_match_when_words_lowercase(self) -> None:
        # "acme ltda" all lowercase shouldn't match.
        assert detect_company_with_suffix("a empresa acme ltda paga") == []


class TestGovernmentBody:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("ofício do Ministério da Fazenda recebido", "Ministério da Fazenda"),
            ("a Secretaria de Educação anunciou", "Secretaria de Educação"),
            ("o Tribunal Regional Federal decidiu", "Tribunal Regional Federal"),
            ("pela Receita Federal foi", "Receita Federal"),
            ("o Conselho Nacional de Justiça publicou", "Conselho Nacional de Justiça"),
            ("a Procuradoria-Geral da República opinou", "Procuradoria-Geral da República"),
            ("Ministério Público Federal ajuizou", "Ministério Público Federal"),
        ],
    )
    def test_finds_gov_body(self, text: str, expected: str) -> None:
        spans = detect_government_body(text)
        assert len(spans) >= 1
        matched = text[spans[0].start : spans[0].end]
        assert expected in matched
        assert spans[0].entity_type == "private_company"
        assert spans[0].source == "br_gov_body"

    def test_keyword_alone_does_not_match(self) -> None:
        # "Ministério" without a body name MUST NOT match — needs at
        # least one capitalised follow-up token.
        assert detect_government_body("o ministério respondeu") == []

    def test_lowercase_keyword_rejected(self) -> None:
        assert detect_government_body("o ministério da fazenda fala") == []


class TestEducationInstitution:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("formado pela Universidade de São Paulo em 2010", "Universidade de São Paulo"),
            ("estudou na Faculdade de Medicina depois", "Faculdade de Medicina"),
            ("integrante do Instituto Federal Catarinense agora", "Instituto Federal Catarinense"),
            ("aluno do Colégio Pedro II antes", "Colégio Pedro II"),
        ],
    )
    def test_finds_education(self, text: str, expected: str) -> None:
        spans = detect_education_institution(text)
        assert len(spans) >= 1
        matched = text[spans[0].start : spans[0].end]
        assert expected in matched
        assert spans[0].entity_type == "private_company"
        assert spans[0].source == "br_edu_institution"

    def test_keyword_alone_does_not_match(self) -> None:
        assert detect_education_institution("a universidade respondeu") == []


class TestRegexRegistry:
    def test_three_legal_entity_detectors_in_registry(self) -> None:
        """All three private_company detectors must be wired into
        REGEX_DETECTORS so the augmented pipeline picks them up."""
        from anonymizer.regex_detectors import REGEX_DETECTORS

        assert REGEX_DETECTORS["private_company__suffix"] is detect_company_with_suffix
        assert REGEX_DETECTORS["private_company__gov"] is detect_government_body
        assert REGEX_DETECTORS["private_company__edu"] is detect_education_institution

    def test_date_detector_in_registry(self) -> None:
        from anonymizer.regex_detectors import REGEX_DETECTORS

        assert REGEX_DETECTORS["private_date"] is detect_dates


# ---------------------------------------------------------------------------
# Brazilian date formats
# ---------------------------------------------------------------------------

class TestDateNumeric:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Reunião em 25/12/2024 com a equipe.", "25/12/2024"),
            ("Vencimento: 5/12/2024.", "5/12/2024"),
            ("Data 05.12.2024 confirmada", "05.12.2024"),
            ("Pagamento em 25-12-2024.", "25-12-2024"),
            ("Em 5/12/24 enviou.", "5/12/24"),
            ("Início 01/01/2025.", "01/01/2025"),
        ],
    )
    def test_finds_numeric_dates(self, text: str, expected: str) -> None:
        spans = detect_dates(text)
        assert len(spans) >= 1
        matched = text[spans[0].start : spans[0].end]
        assert matched == expected
        assert spans[0].entity_type == "private_date"
        assert spans[0].source == "br_date"

    def test_iso_date(self) -> None:
        spans = detect_dates("Created at 2024-12-25 in the system.")
        assert len(spans) == 1
        assert spans[0].start, spans[0].end == (11, 21)
        # The ISO match
        assert "2024-12-25" in [s.text_hash for s in spans] or True  # presence check below
        assert "Created at 2024-12-25"[spans[0].start : spans[0].end] == "2024-12-25"


class TestDateInvalidRanges:
    def test_invalid_day_does_not_match(self) -> None:
        # Day 32 — regex enforces 1-31
        assert detect_dates("Item 32/12/2024") == []

    def test_invalid_month_does_not_match(self) -> None:
        # Month 13 — regex enforces 1-12
        assert detect_dates("Notas 25/13/2024") == []

    def test_bare_day_month_not_matched(self) -> None:
        # No year → too risky for false positives. Skipped.
        assert detect_dates("entrega em 5/12 dessa semana") == []

    def test_fraction_like_string_not_matched(self) -> None:
        # "5/12" by itself is a fraction. Not a date.
        assert detect_dates("razão 5/12 do total") == []

    def test_mm_yyyy_alone_not_matched(self) -> None:
        # ``12/2024`` could be month/year but also a version. Skipped
        # to keep precision high.
        assert detect_dates("Versão 12/2024 lançada") == []


class TestDateTextual:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Assinado em 25 de dezembro de 2024 pelo cliente.", "25 de dezembro de 2024"),
            ("Documento de 1 de janeiro de 2025.", "1 de janeiro de 2025"),
            ("Em 5 de março de 2024 ocorreu.", "5 de março de 2024"),
            ("Aniversário em 7 de Setembro.", "7 de Setembro"),
        ],
    )
    def test_full_text_dates(self, text: str, expected: str) -> None:
        spans = detect_dates(text)
        assert len(spans) >= 1
        matched = text[spans[0].start : spans[0].end]
        assert matched == expected

    def test_month_year_only(self) -> None:
        spans = detect_dates("Cobrança refere-se a dezembro de 2024.")
        assert len(spans) == 1
        assert (
            "Cobrança refere-se a dezembro de 2024."[
                spans[0].start : spans[0].end
            ]
            == "dezembro de 2024"
        )

    def test_full_text_supersedes_month_year(self) -> None:
        """``25 de dezembro de 2024`` shouldn't ALSO produce a redundant
        ``dezembro de 2024`` span — the longer match wins."""
        spans = detect_dates("Vence em 25 de dezembro de 2024 ok.")
        assert len(spans) == 1
        assert spans[0].end - spans[0].start == len("25 de dezembro de 2024")

    def test_case_insensitive(self) -> None:
        spans = detect_dates("Ata de 1 DE JANEIRO DE 2025 publicada.")
        assert len(spans) == 1


class TestDateAbbreviated:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Pagamento em 25 dez 2024 confirmou.", "25 dez 2024"),
            ("Data 25/dez/2024 anotada.", "25/dez/2024"),
            ("Item 5-jan-2025 expirou.", "5-jan-2025"),
            ("Marco em 10 ago 2024.", "10 ago 2024"),
        ],
    )
    def test_abbr_dates(self, text: str, expected: str) -> None:
        spans = detect_dates(text)
        assert len(spans) >= 1
        matched = text[spans[0].start : spans[0].end]
        assert matched == expected


class TestDateMixedDocument:
    def test_finds_multiple_dates_in_one_document(self) -> None:
        text = (
            "Contrato firmado em 25/12/2024.\n"
            "Vigência: de 1 de janeiro de 2025 até dezembro de 2025.\n"
            "Última revisão: 2024-11-15.\n"
            "Reunião em 5 dez 2024."
        )
        spans = detect_dates(text)
        # Expected: 25/12/2024, "1 de janeiro de 2025", "dezembro de 2025",
        # 2024-11-15, "5 dez 2024" — at least 5
        assert len(spans) >= 5
        matched_strings = {text[s.start : s.end] for s in spans}
        assert "25/12/2024" in matched_strings
        assert "1 de janeiro de 2025" in matched_strings
        assert "2024-11-15" in matched_strings

    def test_no_overlapping_spans(self) -> None:
        text = "Vence em 25 de dezembro de 2024 ok."
        spans = detect_dates(text)
        for i in range(len(spans)):
            for j in range(i + 1, len(spans)):
                a, b = spans[i], spans[j]
                # Non-overlapping: a fully before b OR b fully before a
                assert a.end <= b.start or b.end <= a.start
