"""Pydantic request/response schemas for the API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_serializer


def _iso_utc(dt: datetime | None) -> str | None:
    """Serialise datetimes as ISO-8601 with explicit UTC offset.

    SQLite drops tzinfo on round-trip so values come back naive even though
    we always write them in UTC. Without an explicit offset the browser
    interprets the string as local time and shifts the displayed clock.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class UploadResponse(BaseModel):
    job_id: str
    status: str
    created_at: datetime

    @field_serializer("created_at")
    def _ser_created_at(self, dt: datetime) -> str | None:
        return _iso_utc(dt)


JobMode = Literal["anonymization", "reversible_pseudonymization"]


class JobStatus(BaseModel):
    """Read-side projection of JobModel — never includes raw text."""
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    status: str
    mode: JobMode = "anonymization"
    decision: str | None = None
    risk_level: str | None = None
    risk_score: float | None = None
    file_format: str
    file_hash: str
    file_size: int
    source_filename: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None

    @field_serializer("created_at", "updated_at", "completed_at")
    def _ser_dt(self, dt: datetime | None) -> str | None:
        return _iso_utc(dt)


class ReviewRequest(BaseModel):
    """Body for /approve and /reject endpoints."""
    reviewer: str | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Per-span review events
# ---------------------------------------------------------------------------

ReviewEventType = Literal[
    "accept", "edit", "false_positive", "missed_pii", "comment"
]


class ManualRedactionRequest(BaseModel):
    """Body for POST /jobs/{id}/manual-redactions.

    start/end index into the *current* redacted text. ``expected_text`` is
    the literal selected fragment — the backend uses it as the source of
    truth and replaces every occurrence in the document, which is more
    robust than relying on offsets alone.
    """
    start: int
    end: int
    entity_type: str
    expected_text: str | None = None
    reviewer: str | None = None
    note: str | None = None


class ReviewEventRequest(BaseModel):
    """Body for POST /jobs/{id}/review-events.

    span_index references the position in the applied_spans list returned by
    /report. For doc-level events (missed_pii, free comment) span_index may
    be omitted and the relevant region is recorded in payload.
    """
    event_type: ReviewEventType
    span_index: int | None = None
    reviewer: str | None = None
    note: str | None = None
    payload: dict[str, Any] | None = None


class ReviewEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    span_index: int | None
    reviewer: str | None
    note: str | None
    payload: str | None
    created_at: datetime

    @field_serializer("created_at")
    def _ser_created_at(self, dt: datetime) -> str | None:
        return _iso_utc(dt)


# ---------------------------------------------------------------------------
# Runtime settings (which detectors are enabled)
# ---------------------------------------------------------------------------

class RuntimeSettingsSchema(BaseModel):
    enabled_detectors: list[str]


class DetectorInfo(BaseModel):
    """Catalogue entry for the settings UI."""
    kind: str
    enabled: bool


class SettingsCatalogue(BaseModel):
    """Full payload returned by GET /settings — current state plus every kind."""
    enabled_detectors: list[str]
    available_detectors: list[str]


# ---------------------------------------------------------------------------
# Reversible pseudonymization (the round-trip flow)
# ---------------------------------------------------------------------------

class PlaceholderInfo(BaseModel):
    placeholder: str        # e.g. "[PESSOA_01]"
    original_text: str      # the value the placeholder represents
    entity_type: str
    occurrences: int        # how many times this placeholder appears in the text


class ReversiblePackage(BaseModel):
    """Payload to hand off to whatever external system will process the text."""
    pseudonymized_text: str
    instructions: str
    placeholders: list[PlaceholderInfo]


class ProcessedTextRequest(BaseModel):
    """Body for /reversible/validate and /reversible/restore."""
    processed_text: str


class PlaceholderCount(BaseModel):
    placeholder: str
    expected: int
    actual: int


class ValidationReport(BaseModel):
    valid: bool
    missing: list[PlaceholderCount]      # appears fewer times than expected
    duplicated: list[PlaceholderCount]   # appears more times than expected
    unexpected: list[str]                # placeholder pattern not in original


class RestoredResult(BaseModel):
    restored_text: str
    validation: ValidationReport


class ReversibleStatus(BaseModel):
    mode: str
    available: bool                       # whether reversible operations are allowed
    has_restored: bool                    # whether a restored.txt exists on disk
    placeholder_count: int


# ---------------------------------------------------------------------------
# Detector comparison (diagnostic OPF-vs-regex mode)
# ---------------------------------------------------------------------------

ComparisonStatusName = Literal[
    "both", "opf_only", "regex_only", "partial_overlap", "type_conflict"
]


class DetectorSpanViewSchema(BaseModel):
    start: int
    end: int
    entity_type: str
    confidence: float | None = None
    source: str | None = None
    text_preview: str | None = None


class ComparisonItemSchema(BaseModel):
    block_id: str
    status: ComparisonStatusName
    opf_span: DetectorSpanViewSchema | None = None
    regex_span: DetectorSpanViewSchema | None = None
    overlap_ratio: float
    context_preview: str | None = None


class ComparisonSummarySchema(BaseModel):
    total: int = 0
    both: int = 0
    opf_only: int = 0
    regex_only: int = 0
    partial_overlap: int = 0
    type_conflict: int = 0


class EntityTypeComparisonSchema(BaseModel):
    entity_type: str
    summary: ComparisonSummarySchema


class ComparisonBlockSchema(BaseModel):
    block_id: str
    text: str


class DetectorComparisonReportSchema(BaseModel):
    job_id: str
    summary: ComparisonSummarySchema
    by_entity_type: list[EntityTypeComparisonSchema] = []
    items: list[ComparisonItemSchema] = []
    blocks: list[ComparisonBlockSchema] = []


class OPFStatusSchema(BaseModel):
    """Response for ``GET/POST /api/opf/{status,enable,disable}``."""

    # ``False`` when the API was started in mock mode — the toggle is
    # hidden in the UI and POSTs to /enable return 409.
    available: bool
    # ``True`` while the subprocess is up and accepting detections.
    enabled: bool
    # ``True`` between ``enable()`` being called and the worker emitting
    # ``ready`` (model load takes ~30–60s). Frontend polls /status here.
    loading: bool
    # Last error from a failed enable attempt; cleared on a successful one.
    error: str | None = None
    # Number of jobs currently leasing the OPF subprocess. ``disable``
    # waits for this to hit zero (or times out).
    in_flight_jobs: int = 0
