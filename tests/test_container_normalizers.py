"""Unit tests for the per-entity-type value normalisers.

Two detections refer to the same real entity (and therefore must share
a marker within a container) iff their normalised values are equal.
These tests pin down the equivalence classes the resolver depends on.
"""
from __future__ import annotations

import pytest

from anonymizer_api.containers.normalizers import (
    get_normalizer,
    normalize,
    normalize_company,
    normalize_default,
    normalize_document_number,
    normalize_email,
    normalize_person,
    normalize_phone,
)


class TestDocumentNumber:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("123.456.789-00", "12345678900"),
            ("12345678900", "12345678900"),
            (" 12.345.678/0001-90 ", "12345678000190"),
            ("11.222.333-4", "112223334"),
        ],
    )
    def test_strips_non_digits(self, raw: str, expected: str) -> None:
        assert normalize_document_number(raw) == expected

    def test_no_digits_yields_empty(self) -> None:
        assert normalize_document_number("---") == ""


class TestEmail:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Foo@Example.com", "foo@example.com"),
            ("  bar@example.com  ", "bar@example.com"),
            ("BAR@EXAMPLE.COM", "bar@example.com"),
        ],
    )
    def test_trims_and_lowers(self, raw: str, expected: str) -> None:
        assert normalize_email(raw) == expected


class TestPhone:
    def test_strips_non_digits(self) -> None:
        assert normalize_phone("(11) 99876-5432") == "11998765432"
        assert normalize_phone("+55 11 99876-5432") == "5511998765432"


class TestPerson:
    def test_strips_diacritics_and_collapses_whitespace(self) -> None:
        assert normalize_person("João  da Silva") == "joao da silva"
        assert normalize_person("JOAO DA SILVA") == "joao da silva"
        assert normalize_person("  joao da   silva  ") == "joao da silva"

    def test_unicode_uppercase_with_diacritics(self) -> None:
        assert normalize_person("MARIA JOSÉ") == "maria jose"

    def test_keeps_internal_spaces_collapsed(self) -> None:
        assert normalize_person("Ana\tMaria  Lima") == "ana maria lima"


class TestCompany:
    def test_uses_same_rule_as_person(self) -> None:
        # Sprint 2 keeps the rules identical so callers can rely on
        # ``Joao Silva`` (person) and ``Joao Silva`` (company) producing
        # different markers because the entity_type differs, not because
        # of the normalisation.
        assert normalize_company("ACME LTDA") == normalize_person("ACME LTDA")


class TestDefault:
    def test_trim_lower_collapse(self) -> None:
        assert normalize_default("  Foo Bar  ") == "foo bar"
        assert normalize_default("FOO\n BAR") == "foo bar"


class TestRegistry:
    @pytest.mark.parametrize(
        "entity_type,fn",
        [
            ("cpf", normalize_document_number),
            ("cnpj", normalize_document_number),
            ("private_email", normalize_email),
            ("private_phone", normalize_phone),
            ("private_person", normalize_person),
        ],
    )
    def test_registry_returns_expected_function(
        self, entity_type: str, fn
    ) -> None:
        assert get_normalizer(entity_type) is fn

    def test_unknown_type_falls_back_to_default(self) -> None:
        assert get_normalizer("totally_unknown_kind") is normalize_default

    def test_normalize_helper_dispatches(self) -> None:
        assert normalize("cpf", "123.456.789-00") == "12345678900"
        assert normalize("private_email", "Foo@Example.com") == "foo@example.com"
