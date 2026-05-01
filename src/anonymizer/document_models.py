from __future__ import annotations

from dataclasses import dataclass, field

# Separator inserted between blocks when rebuilding the full document text.
BLOCK_SEPARATOR = "\n\n"


@dataclass
class DocumentBlock:
    block_id: str
    page: int | None  # 1-based; None for formats without page concept
    text: str
    start_offset: int  # character offset in ExtractionResult.full_text
    end_offset: int    # exclusive


@dataclass
class ExtractionResult:
    blocks: list[DocumentBlock]
    # Canonical concatenation: BLOCK_SEPARATOR.join(b.text for b in blocks)
    # block.start/end_offset index into this string.
    full_text: str


@dataclass
class JobMetadata:
    job_id: str
    source_file: str
    file_hash: str   # SHA-256 of original file bytes
    file_size: int
    format: str      # file extension without dot, e.g. "docx"
    block_count: int
    created_at: str  # ISO-8601 UTC
    policy: str      # path to the policy file used
    stats: dict[str, int] = field(default_factory=dict)


@dataclass
class PipelineResult:
    job_id: str
    redacted_text: str
    applied_spans: list[dict]   # serialisable; document-level offsets
    metadata: JobMetadata
    # Populated by DocumentPipeline.run(); typed as ``Any`` here to avoid a
    # circular import with anonymizer.verification.
    verification: object | None = None
