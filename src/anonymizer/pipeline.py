"""DocumentPipeline: orchestrates extraction, detection, and redaction."""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .client import PrivacyFilterClient
from .document_models import (
    BLOCK_SEPARATOR,
    ExtractionResult,
    JobMetadata,
    PipelineResult,
)
from .extractors.base import BaseExtractor, UnsupportedFormatError
from .extractors.docx import DocxExtractor
from .extractors.image import ImageExtractor
from .extractors.pdf import PdfExtractor
from .extractors.rtf import RtfExtractor
from .extractors.txt import TxtExtractor
from .extractors.xls import XlsExtractor
from .extractors.xlsx import XlsxExtractor
from .policy import Policy
from .redactor import Redactor
from .risk import VerificationConfig
from .verification import Verifier, VerificationReport

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[BaseExtractor]] = {
    ext: cls
    for cls in [
        TxtExtractor,
        PdfExtractor,
        DocxExtractor,
        XlsxExtractor,
        XlsExtractor,
        ImageExtractor,
        RtfExtractor,
    ]
    for ext in cls.supported_extensions
}

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(_REGISTRY)


def extract_document(input_path: Path) -> ExtractionResult:
    """Extract a document into ``ExtractionResult.blocks`` without redaction.

    Public helper used by the diagnostic detector-comparison flow, which
    needs the raw blocks to feed two detectors side by side. Raises
    ``UnsupportedFormatError`` if the extension isn't in ``ALLOWED_EXTENSIONS``.
    """
    suffix = input_path.suffix.lower()
    if suffix not in _REGISTRY:
        raise UnsupportedFormatError(
            f"Extension {suffix!r} is not supported. "
            f"Allowed: {sorted(ALLOWED_EXTENSIONS)}"
        )
    extractor = _REGISTRY[suffix]()
    return extractor.extract(input_path)

DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB

# Number of characters of surrounding context captured per span. Used by the
# review UI to show the reviewer the original PII value with enough context
# to judge whether the detection is correct.
_CONTEXT_CHARS = 50


class FileTooLargeError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class DocumentPipeline:
    def __init__(
        self,
        client: PrivacyFilterClient,
        policy: Policy,
        output_dir: Path,
        max_bytes: int = DEFAULT_MAX_BYTES,
        verifier: Verifier | None = None,
    ) -> None:
        self._client = client
        self._policy = policy
        self._redactor = Redactor(policy)
        self._output_dir = output_dir
        self._max_bytes = max_bytes
        # If no verifier is supplied, build one from the policy's verification
        # section, falling back to the library defaults.
        self._verifier = verifier or Verifier(
            client=client,
            config=policy.verification or VerificationConfig.default(),
        )

    def run(self, input_path: Path, policy_path: str = "") -> PipelineResult:
        # 1. Validate
        suffix = input_path.suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise UnsupportedFormatError(
                f"Extension {suffix!r} is not supported. "
                f"Allowed: {sorted(ALLOWED_EXTENSIONS)}"
            )

        file_size = input_path.stat().st_size
        if file_size > self._max_bytes:
            raise FileTooLargeError(
                f"{input_path.name!r} is {file_size} bytes, "
                f"exceeding the limit of {self._max_bytes} bytes"
            )

        # Log only path and size — never file content.
        logger.info("Pipeline start file=%s size=%d bytes", input_path, file_size)

        # 2. Hash
        file_hash = _sha256_file(input_path)
        job_id = str(uuid.uuid4())

        # 3. Extract
        extractor = _REGISTRY[suffix]()
        try:
            extraction: ExtractionResult = extractor.extract(input_path)
        except UnsupportedFormatError:
            raise
        except Exception as exc:
            raise UnsupportedFormatError(
                f"Failed to extract {input_path.name!r}: {type(exc).__name__}"
            ) from exc

        logger.info(
            "Extracted job_id=%s blocks=%d format=%s",
            job_id,
            len(extraction.blocks),
            suffix.lstrip("."),
        )

        # 4+5. Detect + redact per block, track document-level spans.
        redacted_blocks: list[str] = []
        all_applied_spans: list[dict] = []
        combined_stats: dict[str, int] = {}

        for block in extraction.blocks:
            detected = self._client.detect(block.text)
            result = self._redactor.redact(block.text, detected)

            redacted_blocks.append(result.redacted_text)

            for span in result.applied_spans:
                ctx_start = max(0, span.start - _CONTEXT_CHARS)
                ctx_end = min(len(block.text), span.end + _CONTEXT_CHARS)
                all_applied_spans.append(
                    {
                        "block_id": block.block_id,
                        "page": block.page,
                        "doc_start": block.start_offset + span.start,
                        "doc_end": block.start_offset + span.end,
                        "local_start": span.start,
                        "local_end": span.end,
                        "entity_type": span.entity_type,
                        "strategy": span.strategy,
                        "replacement": span.replacement,
                        # Detection provenance — which detector produced this
                        # span. Lets the review UI report whether a finding
                        # came from the OPF model or a deterministic regex.
                        "source": span.source,
                        "confidence": span.confidence,
                        # Original PII fragment + surrounding context.
                        "original_text": block.text[span.start : span.end],
                        "original_context_before": block.text[
                            ctx_start : span.start
                        ],
                        "original_context_after": block.text[
                            span.end : ctx_end
                        ],
                    }
                )

            for entity_type, count in result.stats.items():
                combined_stats[entity_type] = combined_stats.get(entity_type, 0) + count

            logger.debug(
                "Block job_id=%s block_id=%s spans=%d",
                job_id,
                block.block_id,
                len(result.applied_spans),
            )

        # 6. Assemble
        redacted_text = BLOCK_SEPARATOR.join(redacted_blocks)

        # 6b. Compute the authoritative position of each span in the
        # final redacted text. This lets the UI render highlights without
        # having to reverse-engineer offsets after manual redactions.
        sorted_idx = sorted(
            range(len(all_applied_spans)),
            key=lambda i: all_applied_spans[i]["doc_start"],
        )
        running_delta = 0
        for i in sorted_idx:
            s = all_applied_spans[i]
            rstart = s["doc_start"] + running_delta
            s["redacted_start"] = rstart
            s["redacted_end"] = rstart + len(s["replacement"])
            running_delta += len(s["replacement"]) - (s["doc_end"] - s["doc_start"])

        # 7. Verify (second pass + deterministic rules + risk scoring)
        verification = self._verifier.verify(redacted_text)

        # 8. Build result and save artefacts
        metadata = JobMetadata(
            job_id=job_id,
            source_file=str(input_path.resolve()),
            file_hash=file_hash,
            file_size=file_size,
            format=suffix.lstrip("."),
            block_count=len(extraction.blocks),
            created_at=datetime.now(tz=timezone.utc).isoformat(),
            policy=policy_path or str(self._policy),
            stats=combined_stats,
        )

        pipeline_result = PipelineResult(
            job_id=job_id,
            redacted_text=redacted_text,
            applied_spans=all_applied_spans,
            metadata=metadata,
            verification=verification,
        )

        self._save(pipeline_result, verification)

        logger.info(
            "Pipeline done job_id=%s total_spans=%d decision=%s",
            job_id,
            len(all_applied_spans),
            verification.risk_assessment.decision,
        )
        return pipeline_result

    def _save(self, result: PipelineResult, verification: VerificationReport) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)

        (self._output_dir / "redacted.txt").write_text(
            result.redacted_text, encoding="utf-8"
        )
        (self._output_dir / "spans.json").write_text(
            json.dumps(result.applied_spans, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self._output_dir / "job_metadata.json").write_text(
            json.dumps(asdict(result.metadata), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self._output_dir / "verification_report.json").write_text(
            json.dumps(verification.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Artefacts saved to %s", self._output_dir)
