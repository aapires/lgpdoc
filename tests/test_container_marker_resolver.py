"""Tests for the container-scoped marker resolver.

These tests pin the four invariants that make the feature meaningful:

1. Same normalised value within the same container always returns the
   same marker (re-detection reuses).
2. Same value in different containers gets independent markers (no
   cross-container leakage).
3. Marker indices are allocated per-container, per-label.
4. Markers never carry semantic hints — labels come from the dictionary
   in ``marker_resolver.LABEL_FOR_ENTITY_TYPE``.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from anonymizer_api.containers.marker_resolver import (
    LABEL_FOR_ENTITY_TYPE,
    MarkerResolver,
    format_marker,
    label_for,
)
from anonymizer_api.db.database import Database
from anonymizer_api.db.models import (
    CONTAINER_STATUS_ACTIVE,
    ContainerModel,
    ContainerMappingEntryModel,
)
from anonymizer_api.db.repositories import (
    ContainerMappingEntryRepository,
    ContainerRepository,
)


@pytest.fixture()
def db(tmp_path: Path) -> Session:
    database = Database(f"sqlite:///{tmp_path}/marker.db")
    database.create_all()
    session = database.session()
    yield session
    session.close()


def _make_container(db: Session, *, name: str) -> ContainerModel:
    repo = ContainerRepository(db)
    return repo.create(
        container_id=str(uuid.uuid4()),
        name=name,
        description=None,
        status=CONTAINER_STATUS_ACTIVE,
    )


@pytest.fixture()
def container_a(db: Session) -> ContainerModel:
    return _make_container(db, name="A")


@pytest.fixture()
def container_b(db: Session) -> ContainerModel:
    return _make_container(db, name="B")


@pytest.fixture()
def repo(db: Session) -> ContainerMappingEntryRepository:
    return ContainerMappingEntryRepository(db)


# ---------------------------------------------------------------------------
# Invariant 1: same normalised value reuses the marker
# ---------------------------------------------------------------------------

class TestReuseWithinContainer:
    def test_same_value_returns_same_marker(
        self,
        repo: ContainerMappingEntryRepository,
        container_a: ContainerModel,
    ) -> None:
        resolver = MarkerResolver(repo, container_a.container_id)
        first = resolver.resolve(
            entity_type="private_person", original_text="Joao Silva"
        )
        second = resolver.resolve(
            entity_type="private_person", original_text="Joao Silva"
        )
        assert first.marker == second.marker
        assert first.created is True
        assert second.created is False
        assert first.mapping_entry.id == second.mapping_entry.id

    def test_normalisation_collapses_variants(
        self,
        repo: ContainerMappingEntryRepository,
        container_a: ContainerModel,
    ) -> None:
        """Different writings of the same person ('Joao Silva',
        'JOAO SILVA', '  joao   silva  ') must collapse to one marker
        because their ``normalize_person`` outputs match."""
        resolver = MarkerResolver(repo, container_a.container_id)
        m1 = resolver.resolve(
            entity_type="private_person", original_text="Joao Silva"
        ).marker
        m2 = resolver.resolve(
            entity_type="private_person", original_text="JOAO SILVA"
        ).marker
        m3 = resolver.resolve(
            entity_type="private_person", original_text="  joao   silva  "
        ).marker
        assert m1 == m2 == m3

    def test_cpf_punctuation_variants_collapse(
        self,
        repo: ContainerMappingEntryRepository,
        container_a: ContainerModel,
    ) -> None:
        """``111.444.777-35`` and ``11144477735`` are the same CPF —
        both must share the marker."""
        resolver = MarkerResolver(repo, container_a.container_id)
        m1 = resolver.resolve(
            entity_type="cpf", original_text="111.444.777-35"
        ).marker
        m2 = resolver.resolve(
            entity_type="cpf", original_text="11144477735"
        ).marker
        assert m1 == m2

    def test_different_entity_types_get_distinct_markers(
        self,
        repo: ContainerMappingEntryRepository,
        container_a: ContainerModel,
    ) -> None:
        """Even with the same raw string, different entity types must
        not be merged. ``[PESSOA_0001]`` and ``[EMAIL_0001]`` are
        unrelated allocations."""
        resolver = MarkerResolver(repo, container_a.container_id)
        person = resolver.resolve(
            entity_type="private_person", original_text="foo"
        ).marker
        email = resolver.resolve(
            entity_type="private_email", original_text="foo"
        ).marker
        assert person != email
        assert person.startswith("[PESSOA_")
        assert email.startswith("[EMAIL_")


# ---------------------------------------------------------------------------
# Invariant 2: cross-container isolation
# ---------------------------------------------------------------------------

class TestContainerIsolation:
    def test_same_value_different_containers_get_independent_markers(
        self,
        repo: ContainerMappingEntryRepository,
        container_a: ContainerModel,
        container_b: ContainerModel,
    ) -> None:
        """Both containers will emit ``[PESSOA_0001]`` for the first
        person they see — but those are distinct identifiers because
        each lives in a different container row."""
        resolver_a = MarkerResolver(repo, container_a.container_id)
        resolver_b = MarkerResolver(repo, container_b.container_id)

        marker_a = resolver_a.resolve(
            entity_type="private_person", original_text="Joao Silva"
        ).marker
        marker_b = resolver_b.resolve(
            entity_type="private_person", original_text="Maria Souza"
        ).marker

        # Both first allocations are PESSOA_0001 — same string, distinct
        # containers, distinct meanings.
        assert marker_a == "[PESSOA_0001]"
        assert marker_b == "[PESSOA_0001]"

        # And the mapping entries are NOT cross-readable: looking up
        # ``Joao Silva`` from container B returns nothing.
        cross = repo.find_by_normalized(
            container_b.container_id, "private_person", "joao silva"
        )
        assert cross is None

    def test_resolver_writes_only_to_its_own_container(
        self,
        repo: ContainerMappingEntryRepository,
        container_a: ContainerModel,
        container_b: ContainerModel,
    ) -> None:
        resolver_a = MarkerResolver(repo, container_a.container_id)
        resolver_a.resolve(
            entity_type="private_person", original_text="Joao Silva"
        )
        # Container B should still be empty.
        assert (
            repo.list_for_container(container_b.container_id) == []
        )


# ---------------------------------------------------------------------------
# Invariant 3: per-container per-label index allocation
# ---------------------------------------------------------------------------

class TestIndexAllocation:
    def test_indices_are_sequential_per_label(
        self,
        repo: ContainerMappingEntryRepository,
        container_a: ContainerModel,
    ) -> None:
        resolver = MarkerResolver(repo, container_a.container_id)
        m1 = resolver.resolve(
            entity_type="private_person", original_text="A A"
        ).marker
        m2 = resolver.resolve(
            entity_type="private_person", original_text="B B"
        ).marker
        m3 = resolver.resolve(
            entity_type="private_person", original_text="C C"
        ).marker
        assert (m1, m2, m3) == (
            "[PESSOA_0001]",
            "[PESSOA_0002]",
            "[PESSOA_0003]",
        )

    def test_indices_independent_per_label(
        self,
        repo: ContainerMappingEntryRepository,
        container_a: ContainerModel,
    ) -> None:
        """Adding emails to a container that already has people must
        start the EMAIL counter at 0001, not at len(PESSOA)+1."""
        resolver = MarkerResolver(repo, container_a.container_id)
        for n in ("A", "B", "C"):
            resolver.resolve(
                entity_type="private_person", original_text=f"{n} {n}"
            )
        first_email = resolver.resolve(
            entity_type="private_email", original_text="alice@example.com"
        ).marker
        assert first_email == "[EMAIL_0001]"

    def test_marker_uniqueness_constraint_per_container(
        self,
        repo: ContainerMappingEntryRepository,
        container_a: ContainerModel,
        db: Session,
    ) -> None:
        """The DB enforces ``unique(container_id, marker)`` — bypassing
        the resolver and trying to insert a duplicate marker raises."""
        from sqlalchemy.exc import IntegrityError

        resolver = MarkerResolver(repo, container_a.container_id)
        resolver.resolve(
            entity_type="private_person", original_text="Joao Silva"
        )

        # Direct insertion bypassing the resolver.
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        dup = ContainerMappingEntryModel(
            container_id=container_a.container_id,
            entity_type="private_person",
            marker="[PESSOA_0001]",
            original_text="Maria Souza",
            normalized_value="maria souza",
            review_status="auto",
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(dup)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


# ---------------------------------------------------------------------------
# Invariant 4: labels never carry semantic hints
# ---------------------------------------------------------------------------

class TestLabelDictionaryIsClean:
    _MARKER_RE = re.compile(r"^\[[A-Z_]+_\d{4,}\]$")

    def test_label_for_known_types(self) -> None:
        assert label_for("cpf") == "CPF"
        assert label_for("private_person") == "PESSOA"
        assert label_for("private_email") == "EMAIL"
        assert label_for("cnpj") == "CNPJ"

    def test_label_falls_back_to_documento_for_unknown_types(self) -> None:
        # The fallback must NOT echo the entity_type — the type itself
        # could contain hints.
        assert label_for("totally_unknown_kind") == "DOCUMENTO"
        assert label_for("__internal__") == "DOCUMENTO"

    def test_format_marker_shape(self) -> None:
        assert format_marker("PESSOA", 1) == "[PESSOA_0001]"
        assert format_marker("CPF", 42) == "[CPF_0042]"
        assert format_marker("CNPJ", 9999) == "[CNPJ_9999]"

    def test_no_label_contains_real_value_hint(self) -> None:
        """Sweep the dictionary: no label should look like a person name
        or domain (lower-case letters, dots, @-signs, etc.)."""
        for entity_type, label in LABEL_FOR_ENTITY_TYPE.items():
            assert label.isupper() or "_" in label, (
                f"Label for {entity_type!r} ({label!r}) must be uppercase / "
                f"underscored — never derived from real values."
            )
            for char in label:
                assert char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_", (
                    f"Label {label!r} for {entity_type!r} contains "
                    f"forbidden char {char!r}."
                )

    def test_resolver_emits_only_well_formed_markers(
        self,
        repo: ContainerMappingEntryRepository,
        container_a: ContainerModel,
    ) -> None:
        resolver = MarkerResolver(repo, container_a.container_id)
        cases = [
            ("private_person", "Joao Silva"),
            ("cpf", "111.444.777-35"),
            ("private_email", "alice@example.com"),
            ("cnpj", "12.345.678/0001-90"),
            ("totally_unknown_type", "whatever"),
        ]
        for entity_type, raw in cases:
            r = resolver.resolve(entity_type=entity_type, original_text=raw)
            assert self._MARKER_RE.match(r.marker), r.marker
            # Sanity: no part of the original value leaks into the marker
            assert raw.lower() not in r.marker.lower()
