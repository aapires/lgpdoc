"""Unit tests for deterministic verification rules (BR identifiers + secrets)."""
from __future__ import annotations

import pytest

from anonymizer.rules import RuleMatch, run_all_rules
from anonymizer.rules.br_identifiers import (
    _validate_cnpj,
    _validate_cpf,
    find_br_phones,
    find_ceps,
    find_cnpjs,
    find_cpfs,
    find_emails,
)
from anonymizer.rules.secrets import (
    find_api_keys,
    find_bearer_tokens,
    find_jwts,
    find_pem_keys,
)


# ---------------------------------------------------------------------------
# Helpers — generate valid synthetic CPF/CNPJ from arbitrary prefixes
# ---------------------------------------------------------------------------

def _make_cpf(prefix9: str) -> str:
    def calc(d, factor):
        s = sum(int(c) * (factor - i) for i, c in enumerate(d))
        r = (s * 10) % 11
        return r if r < 10 else 0
    dv1 = calc(prefix9, 10)
    dv2 = calc(prefix9 + str(dv1), 11)
    return f"{prefix9}{dv1}{dv2}"


def _make_cnpj(prefix12: str) -> str:
    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    w2 = [6] + w1

    def calc(d, weights):
        s = sum(int(c) * w for c, w in zip(d, weights))
        r = s % 11
        return 0 if r < 2 else 11 - r
    dv1 = calc(prefix12, w1)
    dv2 = calc(prefix12 + str(dv1), w2)
    return f"{prefix12}{dv1}{dv2}"


# ---------------------------------------------------------------------------
# CPF
# ---------------------------------------------------------------------------

class TestCPF:
    def test_known_valid_cpf(self) -> None:
        # 111.444.777-35 is a canonical synthetic test CPF (digits validate)
        assert _validate_cpf("11144477735") is True

    def test_all_same_digits_rejected(self) -> None:
        assert _validate_cpf("11111111111") is False
        assert _validate_cpf("00000000000") is False

    def test_invalid_check_digits_rejected(self) -> None:
        assert _validate_cpf("11144477700") is False

    def test_wrong_length_rejected(self) -> None:
        assert _validate_cpf("123") is False
        assert _validate_cpf("12345678901234") is False

    def test_find_cpf_in_text(self) -> None:
        cpf = _make_cpf("123456789")
        text = f"My CPF is {cpf} for the contract"
        matches = find_cpfs(text, RuleMatch)
        assert len(matches) == 1
        assert matches[0].rule_id == "cpf"
        assert matches[0].severity == "high"

    def test_cpf_with_punctuation(self) -> None:
        text = "CPF: 111.444.777-35"
        matches = find_cpfs(text, RuleMatch)
        assert len(matches) == 1

    def test_invalid_cpf_in_text_not_matched(self) -> None:
        text = "Not a CPF: 111.444.777-00"
        matches = find_cpfs(text, RuleMatch)
        assert matches == []


# ---------------------------------------------------------------------------
# CNPJ
# ---------------------------------------------------------------------------

class TestCNPJ:
    def test_known_valid_cnpj(self) -> None:
        cnpj = _make_cnpj("112223330001")
        assert _validate_cnpj(cnpj) is True

    def test_all_same_digits_rejected(self) -> None:
        assert _validate_cnpj("11111111111111") is False

    def test_find_cnpj_in_text(self) -> None:
        cnpj = _make_cnpj("112223330001")
        formatted = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
        matches = find_cnpjs(f"Empresa CNPJ {formatted}", RuleMatch)
        assert len(matches) == 1
        assert matches[0].rule_id == "cnpj"
        assert matches[0].severity == "high"

    def test_invalid_cnpj_not_matched(self) -> None:
        matches = find_cnpjs("CNPJ 11.222.333/0001-99", RuleMatch)
        assert matches == []


# ---------------------------------------------------------------------------
# CEP
# ---------------------------------------------------------------------------

class TestCEP:
    def test_finds_cep_with_dash(self) -> None:
        matches = find_ceps("CEP 01310-100", RuleMatch)
        assert len(matches) == 1
        assert matches[0].rule_id == "cep"

    def test_no_dash_not_matched(self) -> None:
        # Pattern requires the dash — 8-digit blob is too ambiguous.
        matches = find_ceps("01310100", RuleMatch)
        assert matches == []


# ---------------------------------------------------------------------------
# BR Phone
# ---------------------------------------------------------------------------

class TestBRPhone:
    @pytest.mark.parametrize("phone", [
        "(11) 91234-5678",
        "+55 11 91234-5678",
        "11 91234-5678",
        "(21) 2345-6789",
    ])
    def test_finds_phone(self, phone: str) -> None:
        matches = find_br_phones(f"contato {phone}", RuleMatch)
        assert len(matches) >= 1
        assert any(m.rule_id == "br_phone" for m in matches)

    def test_short_number_rejected(self) -> None:
        # 4-digit extension code shouldn't trigger a phone match.
        matches = find_br_phones("ramal 1234", RuleMatch)
        assert matches == []


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

class TestEmail:
    def test_find_email(self) -> None:
        matches = find_emails("send to user@example.org", RuleMatch)
        assert len(matches) == 1
        assert matches[0].rule_id == "email"

    def test_no_email(self) -> None:
        assert find_emails("no email here", RuleMatch) == []


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

class TestSecrets:
    def test_jwt_detected(self) -> None:
        # synthetic JWT-shaped string (header.payload.signature)
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abcdefghij"
        matches = find_jwts(f"token={jwt}", RuleMatch)
        assert len(matches) == 1
        assert matches[0].severity == "critical"

    def test_bearer_token_detected(self) -> None:
        text = "Authorization: Bearer abcdef1234567890ghijklmnop"
        matches = find_bearer_tokens(text, RuleMatch)
        assert len(matches) == 1
        assert matches[0].rule_id == "bearer_token"

    def test_pem_private_key_detected(self) -> None:
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEAabcdefg\n"
            "-----END RSA PRIVATE KEY-----"
        )
        matches = find_pem_keys(pem, RuleMatch)
        assert len(matches) == 1
        assert matches[0].rule_id == "private_key"

    @pytest.mark.parametrize("key", [
        "sk-abcdefghijklmnopqrstuvwxyz123",
        "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ12345678",
        "AKIAIOSFODNN7EXAMPLE",
        "AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI",
    ])
    def test_api_key_detected(self, key: str) -> None:
        matches = find_api_keys(f"key={key}", RuleMatch)
        assert len(matches) == 1
        assert matches[0].rule_id == "api_key"

    def test_no_false_positive_on_normal_word(self) -> None:
        assert find_api_keys("normal text", RuleMatch) == []
        assert find_jwts("normal text", RuleMatch) == []


# ---------------------------------------------------------------------------
# Combined runner
# ---------------------------------------------------------------------------

def test_run_all_rules_aggregates() -> None:
    cpf = _make_cpf("987654321")
    text = (
        f"Cliente CPF {cpf} email a@b.org "
        "Bearer abcdef1234567890ghijklmnop "
        "CEP 01310-100"
    )
    matches = run_all_rules(text)
    rule_ids = {m.rule_id for m in matches}
    assert "cpf" in rule_ids
    assert "email" in rule_ids
    assert "bearer_token" in rule_ids
    assert "cep" in rule_ids
