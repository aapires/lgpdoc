"""SQLAlchemy 2.0 ORM models for the anonymizer API."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobModel(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    source_filename: Mapped[str] = mapped_column(String, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    file_format: Mapped[str] = mapped_column(String(10), nullable=False)
    quarantine_path: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    redacted_path: Mapped[str | None] = mapped_column(String, nullable=True)
    spans_path: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_path: Mapped[str | None] = mapped_column(String, nullable=True)
    report_path: Mapped[str | None] = mapped_column(String, nullable=True)
    restored_path: Mapped[str | None] = mapped_column(String, nullable=True)

    # Processing mode chosen at upload time.
    #   "anonymization"               — irreversible (default, current behaviour)
    #   "reversible_pseudonymization" — placeholders can be restored later
    mode: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="anonymization",
        server_default="anonymization",
    )

    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    # Was the OPF model active when this job was processed? Captured at
    # the start of the pipeline run via OPFManager.acquire(). ``None``
    # for legacy rows from before this column existed.
    opf_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    policy_version_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("policy_versions.id"), nullable=True
    )

    # Optional link to a pseudonymization container. Jobs created via
    # ``POST /api/containers/{id}/documents/raw`` carry this; standalone
    # jobs (the original three modes) leave it null. Listing endpoints
    # filter on this so container-bound jobs don't leak into the regular
    # documents view.
    container_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("containers.container_id"),
        nullable=True,
        index=True,
    )


class DetectedSpanModel(Base):
    __tablename__ = "detected_spans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.job_id"), nullable=False, index=True
    )
    block_id: Mapped[str] = mapped_column(String, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doc_start: Mapped[int] = mapped_column(Integer, nullable=False)
    doc_end: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(20), nullable=False)
    replacement: Mapped[str] = mapped_column(String, nullable=False)


class ReviewEventModel(Base):
    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.job_id"), nullable=False, index=True
    )
    # Doc-level: auto_approved | approved | rejected | blocked
    # Span-level: accept | edit | false_positive | missed_pii | comment
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    reviewer: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    # Index into the job's applied_spans list — null for doc-level events
    span_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # JSON-encoded payload (e.g. {"replacement": "...", "missed_start": 12, ...})
    payload: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class PolicyVersionModel(Base):
    __tablename__ = "policy_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    policy_path: Mapped[str] = mapped_column(String, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Containers (Pseudonymization workspaces)
# ---------------------------------------------------------------------------
#
# Each ``Container`` groups documents that share an analysis context.
# Marker resolution (e.g. ``[PESSOA_0001]`` always pointing to the same
# normalized person) is scoped strictly to the container — the same marker
# in two containers MAY point to different real values. All queries on
# mapping entries / documents MUST filter by ``container_id``.
#
# Sprint 1 ships only ``ContainerModel`` (CRUD foundation). Sprint 2 will
# add ContainerDocumentModel + ContainerMappingEntryModel + ContainerSpanModel.

CONTAINER_STATUS_ACTIVE = "active"
CONTAINER_STATUS_ARCHIVED = "archived"


class ContainerModel(Base):
    __tablename__ = "containers"

    container_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    # Status of the workspace itself (not of individual documents).
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CONTAINER_STATUS_ACTIVE,
        server_default=CONTAINER_STATUS_ACTIVE,
        index=True,
    )
    # JSON-encoded free-form settings (e.g. enabled marker types,
    # normalisation overrides). Sprint 1 stores ``"{}"`` by default.
    settings_json: Mapped[str] = mapped_column(
        String, nullable=False, default="{}", server_default="{}"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )


# ---------------------------------------------------------------------------
# Sprint 2 — documents, mapping entries, and spans inside a container.
# ---------------------------------------------------------------------------
#
# Marker resolution rule (enforced by ContainerMappingEntryModel uniqueness):
#   * For each (container_id, entity_type, normalized_value) there is at
#     most one mapping entry. Re-detection of the same value reuses the
#     same marker.
#   * Markers are unique within a container — two entities with the same
#     entity_type get different indices ([PESSOA_0001], [PESSOA_0002]).
#   * The same marker in different containers may point to different
#     real values — never query mapping entries without filtering by
#     container_id.
#

CONTAINER_DOCUMENT_SOURCE_RAW = "raw_sensitive_document"
CONTAINER_DOCUMENT_SOURCE_PSEUDONYMIZED = "already_pseudonymized_document"

CONTAINER_DOCUMENT_STATUS_PENDING = "pending"
CONTAINER_DOCUMENT_STATUS_PROCESSING = "processing"
# Detection ran; user must approve / fix false positives before the
# document is promoted to ``ready``. The underlying job is in
# ``awaiting_review`` while the container doc is in this status.
CONTAINER_DOCUMENT_STATUS_PENDING_REVIEW = "pending_review"
CONTAINER_DOCUMENT_STATUS_READY = "ready"
CONTAINER_DOCUMENT_STATUS_REJECTED = "rejected"
CONTAINER_DOCUMENT_STATUS_FAILED = "failed"


class ContainerDocumentModel(Base):
    __tablename__ = "container_documents"

    document_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    container_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("containers.container_id"),
        nullable=False,
        index=True,
    )
    # The job that drives the review / pseudonymisation pipeline for
    # this document. Set when the container document is uploaded as
    # raw_sensitive_document. Null for already-pseudonymized imports
    # (Sprint 3) which skip the review flow.
    job_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("jobs.job_id"),
        nullable=True,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    # ``raw_sensitive_document`` flows through the pseudonymization pipeline.
    # ``already_pseudonymized_document`` (Sprint 3) skips detection and
    # only validates markers.
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # User-set role within the case: 'source' / 'analysis' / 'summary' /
    # 'report' / 'edited_version' / 'other'. Free-form for Sprint 2.
    role: Mapped[str] = mapped_column(
        String(40), nullable=False, default="source", server_default="source"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CONTAINER_DOCUMENT_STATUS_PENDING,
        server_default=CONTAINER_DOCUMENT_STATUS_PENDING,
    )

    file_format: Mapped[str] = mapped_column(String(10), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    quarantine_path: Mapped[str] = mapped_column(String, nullable=False)
    pseudonymized_path: Mapped[str | None] = mapped_column(String, nullable=True)

    # JSON-encoded validation summary for already-pseudonymized documents
    # (Sprint 3). Sprint 2 stores ``"{}"``.
    validation_summary_json: Mapped[str] = mapped_column(
        String, nullable=False, default="{}", server_default="{}"
    )

    error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )


class ContainerMappingEntryModel(Base):
    __tablename__ = "container_mapping_entries"
    __table_args__ = (
        UniqueConstraint(
            "container_id", "marker", name="uq_container_marker"
        ),
        Index(
            "ix_container_mapping_lookup",
            "container_id",
            "entity_type",
            "normalized_value",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    container_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("containers.container_id"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    marker: Mapped[str] = mapped_column(String(80), nullable=False)
    # Authoritative original value (sensitive). Only ever returned by the
    # explicit "sensitive export" endpoint or by restoration — never logged.
    original_text: Mapped[str] = mapped_column(String, nullable=False)
    # Normalised key for marker lookup (digits-only CPF, lower-cased email,
    # etc.). The same normalised value within the same container always
    # maps to the same marker.
    normalized_value: Mapped[str] = mapped_column(String, nullable=False)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="auto", server_default="auto"
    )
    detection_source: Mapped[str | None] = mapped_column(String, nullable=True)
    created_from_document_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )


class ContainerSpanModel(Base):
    __tablename__ = "container_spans"
    __table_args__ = (
        Index("ix_container_spans_doc", "container_document_id"),
        Index("ix_container_spans_mapping", "mapping_entry_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    container_document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("container_documents.document_id"),
        nullable=False,
    )
    mapping_entry_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("container_mapping_entries.id"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    marker: Mapped[str] = mapped_column(String(80), nullable=False)
    # Span-local original text (denormalised from the mapping entry for
    # fast read in the UI). Treated as sensitive.
    original_text: Mapped[str] = mapped_column(String, nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    detection_source: Mapped[str | None] = mapped_column(String, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="auto", server_default="auto"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
