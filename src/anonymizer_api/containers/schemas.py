"""Pydantic 2 schemas for the containers API.

These mirror the TypeScript types in ``apps/reviewer-ui/src/lib/types.ts``.
Keep both in sync.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


ContainerStatus = Literal["active", "archived"]


class ContainerCreate(BaseModel):
    """POST /api/containers body."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class ContainerUpdate(BaseModel):
    """PATCH /api/containers/{id} body — every field optional."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    status: ContainerStatus | None = None


class ContainerSummary(BaseModel):
    """Read projection used by list and detail endpoints.

    ``document_count`` and ``marker_count`` are aggregated counts that
    Sprint 1 always reports as 0 — Sprint 2 wires the real numbers when
    documents and mapping entries land. The fields are present from the
    start so the frontend doesn't need a schema migration.
    """

    model_config = ConfigDict(from_attributes=True)

    container_id: str
    name: str
    description: str | None = None
    status: ContainerStatus
    document_count: int = 0
    marker_count: int = 0
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _ser_dt(self, dt: datetime | None) -> str | None:
        return _iso_utc(dt)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

ContainerDocumentSourceType = Literal[
    "raw_sensitive_document", "already_pseudonymized_document"
]
ContainerDocumentStatus = Literal[
    "pending",
    "processing",
    "pending_review",
    "ready",
    "rejected",
    "failed",
]


class ContainerDocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: str
    container_id: str
    # Backing job for the review pipeline (Sprint 5). Null for
    # already-pseudonymized imports.
    job_id: str | None = None
    filename: str
    source_type: ContainerDocumentSourceType
    role: str
    status: ContainerDocumentStatus
    file_format: str
    file_hash: str
    file_size: int
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _ser_dt(self, dt: datetime | None) -> str | None:
        return _iso_utc(dt)


# ---------------------------------------------------------------------------
# Mapping (conversion table)
# ---------------------------------------------------------------------------

class ContainerMappingOccurrence(BaseModel):
    """One container document where a marker was observed."""

    document_id: str
    filename: str


class ContainerMappingEntryView(BaseModel):
    """Default mapping projection — INCLUDES ``original_text``.

    The "safe" export endpoint (Sprint 3) will return a stripped variant
    that omits original_text. This default view is what the UI shows on
    ``/containers/{id}/mapping`` and is therefore considered sensitive
    output (it must not be cached publicly, logged, etc.).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    container_id: str
    entity_type: str
    marker: str
    original_text: str
    normalized_value: str
    review_status: str
    detection_source: str | None = None
    created_from_document_id: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    # Documents in the container where this marker was observed.
    # Populated by the route from ``ContainerSpan`` rows + a fallback
    # to ``created_from_document_id`` for entries without spans.
    occurrences: list[ContainerMappingOccurrence] = []

    @field_serializer("first_seen_at", "last_seen_at")
    def _ser_dt(self, dt: datetime | None) -> str | None:
        return _iso_utc(dt)


# ---------------------------------------------------------------------------
# Validation of already-pseudonymized text
# ---------------------------------------------------------------------------

class ValidatePseudonymizedRequest(BaseModel):
    """Body for ``POST /containers/{id}/validate-pseudonymized``.

    The ``processed_text`` is the (potentially externally-edited)
    pseudonymized text the operator wants to check before re-importing
    or restoring."""

    processed_text: str


class ContainerValidationSummary(BaseModel):
    total_well_formed: int = 0
    known_markers: list[str] = []
    unknown_markers: list[str] = []
    malformed_markers: list[str] = []
    is_clean: bool = True


# ---------------------------------------------------------------------------
# Restoration (Sprint 4)
# ---------------------------------------------------------------------------

class RestoreTextRequest(BaseModel):
    """Body for ``POST /containers/{id}/restore/text``."""

    processed_text: str


class ContainerRestoreResult(BaseModel):
    """Outcome of a restore call. ``restored_text`` is sensitive output —
    treat the response body the same way you'd treat the sensitive
    mapping export. Unknown / malformed markers are reported so the UI
    can flag them; they're left untouched in the output."""

    restored_text: str
    replaced_token_count: int = 0
    replaced_unique_count: int = 0
    unknown_markers: list[str] = []
    malformed_markers: list[str] = []
    is_clean: bool = True


# ---------------------------------------------------------------------------
# Pseudonymized review payload
# ---------------------------------------------------------------------------

class ResidualPiiSpanSchema(BaseModel):
    start: int
    end: int
    entity_type: str
    confidence: float | None = None
    detection_source: str | None = None
    fragment: str
    fragment_hash: str


class PseudonymizedReviewPayload(BaseModel):
    """Everything the dedicated pseudonymized review screen needs."""

    document_id: str
    container_id: str
    status: str
    filename: str
    text: str
    validation: ContainerValidationSummary
    residual_pii: list[ResidualPiiSpanSchema] = []


class PseudonymizedManualRedactionRequest(BaseModel):
    fragment: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)


class PseudonymizedManualRedactionResult(BaseModel):
    marker: str
    occurrences: int
    marker_created: bool
    validation: ContainerValidationSummary
