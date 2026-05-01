"""Tests for the marker parser + validator (already-pseudonymized flow).

Pure-function tests live here so the parsing rules are pinned down
independently of the upload pipeline.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from anonymizer_api.containers.marker_resolver import MarkerResolver
from anonymizer_api.containers.validation_service import (
    ValidationSummary,
    find_malformed_marker_candidates,
    parse_markers,
    validate_pseudonymized_text,
)
from anonymizer_api.db.database import Database
from anonymizer_api.db.models import (
    CONTAINER_STATUS_ACTIVE,
    ContainerModel,
)
from anonymizer_api.db.repositories import (
    ContainerMappingEntryRepository,
    ContainerRepository,
)


# ---------------------------------------------------------------------------
# parse_markers
# ---------------------------------------------------------------------------

class TestParseMarkers:
    def test_finds_well_formed_markers_in_order(self) -> None:
        text = "Cliente: [PESSOA_0001] e [PESSOA_0002] revisaram [DOC_0007]."
        markers = parse_markers(text)
        assert [m.text for m in markers] == [
            "[PESSOA_0001]",
            "[PESSOA_0002]",
            "[DOC_0007]",
        ]
        assert markers[0].label == "PESSOA"
        assert markers[0].index == 1
        assert markers[2].label == "DOC"
        assert markers[2].index == 7

    def test_two_digit_indices_are_well_formed(self) -> None:
        # Legacy markers from older mappings — accept 2+ digits.
        text = "[PESSOA_01] e [PESSOA_02]"
        assert [m.text for m in parse_markers(text)] == [
            "[PESSOA_01]",
            "[PESSOA_02]",
        ]

    def test_lowercase_label_is_not_well_formed(self) -> None:
        text = "[joao_silva] cobranca [PESSOA_0001]"
        markers = parse_markers(text)
        assert [m.text for m in markers] == ["[PESSOA_0001]"]

    def test_missing_index_is_not_well_formed(self) -> None:
        assert parse_markers("[PESSOA_]") == []

    def test_no_markers_yields_empty(self) -> None:
        assert parse_markers("nothing to see here") == []


# ---------------------------------------------------------------------------
# find_malformed_marker_candidates
# ---------------------------------------------------------------------------

class TestMalformed:
    def test_lowercase_is_flagged(self) -> None:
        out = find_malformed_marker_candidates("alvo: [joao_silva] aqui")
        assert "[joao_silva]" in out

    def test_well_formed_does_not_appear_in_malformed_list(self) -> None:
        out = find_malformed_marker_candidates("[PESSOA_0001] [DOC_0002]")
        assert out == []

    def test_typo_with_letter_index_is_flagged(self) -> None:
        out = find_malformed_marker_candidates("[CPF_ABC]")
        assert "[CPF_ABC]" in out

    def test_dedupes_repeated_malformed_tokens(self) -> None:
        out = find_malformed_marker_candidates(
            "[joao] foo [joao] bar [joao]"
        )
        assert out == ["[joao]"]


# ---------------------------------------------------------------------------
# validate_pseudonymized_text — known / unknown / malformed against a
# real mapping built via the resolver.
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path: Path) -> Session:
    database = Database(f"sqlite:///{tmp_path}/v.db")
    database.create_all()
    session = database.session()
    yield session
    session.close()


def _make_container(db: Session) -> ContainerModel:
    return ContainerRepository(db).create(
        container_id=str(uuid.uuid4()),
        name="X",
        description=None,
        status=CONTAINER_STATUS_ACTIVE,
    )


class TestValidatePseudonymizedText:
    def test_known_unknown_split(self, db: Session) -> None:
        container = _make_container(db)
        repo = ContainerMappingEntryRepository(db)
        resolver = MarkerResolver(repo, container.container_id)
        # Allocate two markers in this container
        person = resolver.resolve(
            entity_type="private_person", original_text="Joao Silva"
        ).marker  # [PESSOA_0001]
        email = resolver.resolve(
            entity_type="private_email", original_text="alice@example.com"
        ).marker  # [EMAIL_0001]

        text = (
            f"Discussao: {person} comentou em {email}. "
            f"Vi tambem o [PESSOA_0042] mencionado."  # not in mapping
        )
        summary = validate_pseudonymized_text(
            container_id=container.container_id, text=text, repo=repo
        )

        assert person in summary.known_markers
        assert email in summary.known_markers
        assert "[PESSOA_0042]" in summary.unknown_markers
        assert summary.total_well_formed == 3
        assert summary.is_clean is False

    def test_clean_when_every_marker_known_and_no_malformed(
        self, db: Session
    ) -> None:
        container = _make_container(db)
        repo = ContainerMappingEntryRepository(db)
        resolver = MarkerResolver(repo, container.container_id)
        person = resolver.resolve(
            entity_type="private_person", original_text="Joao"
        ).marker

        summary = validate_pseudonymized_text(
            container_id=container.container_id,
            text=f"Cliente {person} confirmou.",
            repo=repo,
        )
        assert summary.is_clean is True
        assert summary.unknown_markers == []
        assert summary.malformed_markers == []

    def test_malformed_tokens_collected(self, db: Session) -> None:
        container = _make_container(db)
        repo = ContainerMappingEntryRepository(db)

        text = "Hello [joao_silva] and [PESSOA_FOO] and [PESSOA_0001]"
        summary = validate_pseudonymized_text(
            container_id=container.container_id, text=text, repo=repo
        )
        assert "[joao_silva]" in summary.malformed_markers
        assert "[PESSOA_FOO]" in summary.malformed_markers
        # The well-formed marker is not malformed (it's just unknown).
        assert "[PESSOA_0001]" not in summary.malformed_markers
        assert "[PESSOA_0001]" in summary.unknown_markers

    def test_isolation_between_containers(self, db: Session) -> None:
        """A marker that exists in container A is unknown in container
        B, even when both have the same marker text — the validator
        looks up by ``container_id`` only."""
        c_a = _make_container(db)
        c_b = _make_container(db)
        repo = ContainerMappingEntryRepository(db)
        # Allocate [PESSOA_0001] in BOTH containers, pointing at
        # different real values.
        marker_a = MarkerResolver(repo, c_a.container_id).resolve(
            entity_type="private_person", original_text="Alice"
        ).marker
        marker_b = MarkerResolver(repo, c_b.container_id).resolve(
            entity_type="private_person", original_text="Bob"
        ).marker
        assert marker_a == marker_b == "[PESSOA_0001]"

        # The text references [PESSOA_0001] AND a B-only marker [DOC_0042]
        # that doesn't exist in A.
        text = "[PESSOA_0001] and [DOC_0042]"
        summary_a = validate_pseudonymized_text(
            container_id=c_a.container_id, text=text, repo=repo
        )
        # In container A: PESSOA_0001 is known (Alice's entry); DOC_0042 is unknown.
        assert "[PESSOA_0001]" in summary_a.known_markers
        assert "[DOC_0042]" in summary_a.unknown_markers


# ---------------------------------------------------------------------------
# Privacy: validator must not echo the document text or originals
# ---------------------------------------------------------------------------

class TestValidatorDoesNotLog:
    def test_no_pii_or_text_in_logs(
        self, db: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        container = _make_container(db)
        repo = ContainerMappingEntryRepository(db)
        MarkerResolver(repo, container.container_id).resolve(
            entity_type="private_person", original_text="Joao Silva"
        )

        sentinel = "Cliente: SUPER_SECRET_PII"
        with caplog.at_level(logging.DEBUG):
            validate_pseudonymized_text(
                container_id=container.container_id,
                text=f"{sentinel} [PESSOA_0001]",
                repo=repo,
            )

        for record in caplog.records:
            msg = record.getMessage()
            assert sentinel not in msg
            assert "Joao Silva" not in msg
