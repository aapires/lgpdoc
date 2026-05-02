"""JobService: orchestrates upload validation, pipeline run, and review actions.

Logging discipline: only metadata (job_id, status, format, hash, size,
decision, score) is emitted. Document content never reaches the logs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

import re

from anonymizer.augmentations import make_augmented_client
from anonymizer.client import PrivacyFilterClient
from anonymizer.detector_comparison import (
    ComparisonBlock,
    DetectorComparisonReport,
    build_comparison_report,
    compare_spans,
)
from anonymizer.extractors.base import UnsupportedFormatError
from anonymizer.pipeline import (
    ALLOWED_EXTENSIONS,
    DocumentPipeline,
    FileTooLargeError,
    extract_document,
)
from anonymizer.policy import EntityPolicy, Policy
from anonymizer.redactor import _pseudonym_for

from ..config import Settings
from ..db.models import JobModel, ReviewEventModel
from ..db.repositories import (
    JobRepository,
    PolicyVersionRepository,
    ReviewRepository,
    SpanRepository,
)
from ..storage import Storage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status enum (string constants — kept simple to avoid migration friction)
# ---------------------------------------------------------------------------

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_AWAITING_REVIEW = "awaiting_review"
STATUS_AUTO_APPROVED = "auto_approved"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"

DOWNLOADABLE_STATUSES = {STATUS_AUTO_APPROVED, STATUS_APPROVED}
TERMINAL_STATUSES = {
    STATUS_AUTO_APPROVED,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_FAILED,
}


class UploadValidationError(ValueError):
    """Raised when the upload fails extension or size checks."""


class InvalidStateError(ValueError):
    """Raised when a transition (approve/reject) is illegal for the current status."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_fragment(text: str) -> str:
    """Stable hash for manual-redaction dedupe — case-folded + whitespace
    collapsed so trivial variations map to the same placeholder."""
    normalised = " ".join(text.lower().split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


_REVERSIBLE_INSTRUCTIONS = """\
Texto pseudonimizado para uso externo

Este texto teve cada dado sensível substituído por marcadores estáveis
(ex.: [PESSOA_01], [EMAIL_02], [CPF_01]). Você pode enviá-lo a qualquer
processo externo (LLM, tradução, resumo, revisão humana, etc.).

Como manter a reversibilidade
1. Não remova, renomeie nem altere os marcadores ([PESSOA_01], [EMAIL_01], ...).
   Cada marcador será trocado pelo dado original na restauração.
2. Pode reordenar parágrafos, traduzir, resumir, ou expandir o texto à
   vontade — desde que os marcadores cheguem inteiros até o fim.
3. Quando o texto processado voltar, cole-o no campo "Texto processado"
   e clique em "Validar marcadores" para conferir.
4. Em seguida clique em "Restaurar dados originais" — o sistema substitui
   cada marcador pelo dado original.

O que a validação verifica
- Marcadores ausentes (algum desapareceu do texto)
- Marcadores duplicados (alguém copiou um marcador a mais)
- Marcadores inesperados (algum padrão [XXX_NN] que não veio do original)
"""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class JobService:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        storage: Storage,
        client: PrivacyFilterClient,
        *,
        opf_manager: "Any | None" = None,
        settings_store: "Any | None" = None,
        on_processing_done: "Callable[[Session, JobModel], None] | None" = None,
        on_approved: "Callable[[Session, JobModel], None] | None" = None,
        on_rejected: "Callable[[Session, JobModel], None] | None" = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.storage = storage
        self.client = client
        # opf_manager + settings_store are optional so existing tests that
        # build JobService directly with a single client still work — those
        # paths skip the OPF lease and use the supplied client as-is.
        self.opf_manager = opf_manager
        self.settings_store = settings_store
        self.jobs = JobRepository(db)
        self.spans = SpanRepository(db)
        self.reviews = ReviewRepository(db)
        self.policies = PolicyVersionRepository(db)
        # Optional lifecycle hooks. The container feature wires these in
        # main.py so jobs that belong to a container get their
        # ContainerDocument row updated as the job moves through the
        # review states. JobService stays ignorant of containers — it
        # just calls the hook with the JobModel and a DB session.
        self._on_processing_done = on_processing_done
        self._on_approved = on_approved
        self._on_rejected = on_rejected

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def submit_upload(
        self,
        filename: str,
        content: bytes,
        *,
        mode: str = "anonymization",
    ) -> JobModel:
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise UploadValidationError(
                f"Extension {ext!r} not allowed. Allowed: {sorted(ALLOWED_EXTENSIONS)}"
            )
        if len(content) > self.settings.max_bytes:
            raise UploadValidationError(
                f"File too large: {len(content)} bytes exceeds limit of "
                f"{self.settings.max_bytes} bytes"
            )
        if mode not in ("anonymization", "reversible_pseudonymization"):
            raise UploadValidationError(
                f"Invalid mode {mode!r}. Allowed: anonymization, reversible_pseudonymization"
            )

        job_id = str(uuid.uuid4())
        quarantine_path = self.storage.quarantine_for(job_id, ext)
        quarantine_path.write_bytes(content)

        file_hash = _sha256_bytes(content)

        # Track the policy YAML version that will be applied.
        policy_path = self.settings.policy_path
        policy_hash = hashlib.sha256(policy_path.read_bytes()).hexdigest()
        pv = self.policies.get_or_create(str(policy_path), policy_hash)

        job = self.jobs.create(
            job_id=job_id,
            status=STATUS_PENDING,
            source_filename=filename,
            file_hash=file_hash,
            file_size=len(content),
            file_format=ext.lstrip("."),
            quarantine_path=str(quarantine_path),
            policy_version_id=pv.id,
            mode=mode,
        )

        logger.info(
            "Upload accepted job_id=%s mode=%s format=%s size=%d hash=%s policy_version=%d",
            job_id,
            mode,
            ext,
            len(content),
            file_hash,
            pv.id,
        )
        return job

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def process(self, job_id: str) -> None:
        """Run the anonymization pipeline for a previously uploaded job.

        This is the entry point used by the background worker.
        """
        job = self.jobs.get(job_id)
        if job is None:
            logger.error("Job not found job_id=%s", job_id)
            return

        self.jobs.update(job_id, status=STATUS_PROCESSING)
        logger.info("Processing started job_id=%s", job_id)

        # Snapshot the detector at job start so toggling OPF off mid-job
        # doesn't change the contract — this job runs to completion with
        # whatever was active when it began. The lease keeps the OPF
        # subprocess alive until release(); ``disable()`` will wait.
        leased_base = (
            self.opf_manager.acquire() if self.opf_manager is not None else None
        )
        if leased_base is not None and self.settings_store is not None:
            run_client: PrivacyFilterClient = make_augmented_client(
                leased_base,
                get_enabled_kinds=self.settings_store.get_enabled_kinds,
            )
        else:
            run_client = self.client

        try:
            try:
                policy = Policy.from_yaml(self.settings.policy_path)
                output_dir = self.storage.output_for(job_id)
                pipeline = DocumentPipeline(
                    client=run_client,
                    policy=policy,
                    output_dir=output_dir,
                    max_bytes=self.settings.max_bytes,
                )
                result = pipeline.run(
                    Path(job.quarantine_path),
                    policy_path=str(self.settings.policy_path),
                )
            except (UnsupportedFormatError, FileTooLargeError) as exc:
                self.jobs.update(
                    job_id,
                    status=STATUS_FAILED,
                    error_message=str(exc),
                    completed_at=datetime.now(timezone.utc),
                )
                logger.error("Processing failed job_id=%s reason=%s", job_id, exc)
                return
            except Exception as exc:  # noqa: BLE001 — record and continue
                self.jobs.update(
                    job_id,
                    status=STATUS_FAILED,
                    error_message=f"{type(exc).__name__}: {exc}",
                    completed_at=datetime.now(timezone.utc),
                )
                logger.exception("Unexpected processing error job_id=%s", job_id)
                return
        finally:
            if leased_base is not None and self.opf_manager is not None:
                self.opf_manager.release(leased_base)

        # Persist spans into DB for queryability.
        span_rows = [
            {
                "job_id": job_id,
                "block_id": s["block_id"],
                "page": s.get("page"),
                "doc_start": s["doc_start"],
                "doc_end": s["doc_end"],
                "entity_type": s["entity_type"],
                "strategy": s["strategy"],
                "replacement": s["replacement"],
            }
            for s in result.applied_spans
        ]
        self.spans.add_many(span_rows)

        verification = result.verification
        risk = verification.risk_assessment
        decision = risk.decision

        # Every successfully processed document goes to manual review
        # regardless of the system's risk assessment. ``decision`` and
        # ``risk_level`` are kept on the job so the reviewer sees the
        # pipeline's recommendation as a *visual signal*, not as an
        # automatic gate. The download step only opens after a human
        # approves via /jobs/{id}/approve.
        new_status = STATUS_AWAITING_REVIEW

        self.jobs.update(
            job_id,
            status=new_status,
            decision=decision,
            risk_level=risk.level,
            risk_score=risk.score,
            redacted_path=str(output_dir / "redacted.txt"),
            spans_path=str(output_dir / "spans.json"),
            metadata_path=str(output_dir / "job_metadata.json"),
            report_path=str(output_dir / "verification_report.json"),
            completed_at=datetime.now(timezone.utc),
        )

        logger.info(
            "Processing done job_id=%s status=%s decision=%s level=%s score=%.2f spans=%d",
            job_id,
            new_status,
            decision,
            risk.level,
            risk.score,
            len(span_rows),
        )

        if self._on_processing_done is not None:
            updated_job = self.jobs.get(job_id)
            if updated_job is not None:
                self._on_processing_done(self.db, updated_job)

    # ------------------------------------------------------------------
    # Review actions
    # ------------------------------------------------------------------

    def approve(
        self, job_id: str, reviewer: str | None, note: str | None
    ) -> JobModel:
        job = self._require_review_state(job_id)
        self.reviews.add(
            job_id=job_id, event_type="approved", reviewer=reviewer, note=note
        )
        updated = self.jobs.update(job_id, status=STATUS_APPROVED)
        assert updated is not None
        logger.info(
            "Job approved job_id=%s reviewer=%s", job_id, reviewer or "<unknown>"
        )
        if self._on_approved is not None:
            self._on_approved(self.db, updated)
        return updated

    def reject(
        self, job_id: str, reviewer: str | None, note: str | None
    ) -> JobModel:
        job = self._require_review_state(job_id)
        self.reviews.add(
            job_id=job_id, event_type="rejected", reviewer=reviewer, note=note
        )
        updated = self.jobs.update(job_id, status=STATUS_REJECTED)
        assert updated is not None
        logger.info(
            "Job rejected job_id=%s reviewer=%s", job_id, reviewer or "<unknown>"
        )
        if self._on_rejected is not None:
            self._on_rejected(self.db, updated)
        return updated

    def revert_approval(
        self, job_id: str, reviewer: str | None, note: str | None
    ) -> JobModel:
        """Toggle off a previous approval — moves the job back to
        ``awaiting_review`` so the reviewer can change their mind. The
        revert is audited as a ReviewEvent."""
        job = self.jobs.get(job_id)
        if job is None:
            raise InvalidStateError(f"Job {job_id!r} not found")
        if job.status != STATUS_APPROVED:
            raise InvalidStateError(
                f"Cannot revert approval: job is in status {job.status!r}, "
                f"not {STATUS_APPROVED!r}"
            )
        self.reviews.add(
            job_id=job_id,
            event_type="approval_reverted",
            reviewer=reviewer,
            note=note,
        )
        updated = self.jobs.update(job_id, status=STATUS_AWAITING_REVIEW)
        assert updated is not None
        logger.info(
            "Approval reverted job_id=%s reviewer=%s",
            job_id,
            reviewer or "<unknown>",
        )
        return updated

    def _require_review_state(self, job_id: str) -> JobModel:
        job = self.jobs.get(job_id)
        if job is None:
            raise InvalidStateError(f"Job {job_id!r} not found")
        if job.status != STATUS_AWAITING_REVIEW:
            raise InvalidStateError(
                f"Cannot review job in status {job.status!r}; "
                f"requires {STATUS_AWAITING_REVIEW!r}"
            )
        return job

    # ------------------------------------------------------------------
    # Download gating
    # ------------------------------------------------------------------

    @staticmethod
    def can_download(job: JobModel) -> bool:
        return job.status in DOWNLOADABLE_STATUSES

    # ------------------------------------------------------------------
    # Listing & per-span review events
    # ------------------------------------------------------------------

    def list_jobs(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> list[JobModel]:
        return self.jobs.list(limit=limit, offset=offset, status=status)

    def record_review_event(
        self,
        job_id: str,
        event_type: str,
        *,
        reviewer: str | None = None,
        note: str | None = None,
        span_index: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ReviewEventModel:
        if self.jobs.get(job_id) is None:
            raise InvalidStateError(f"Job {job_id!r} not found")
        event = self.reviews.add(
            job_id=job_id,
            event_type=event_type,
            reviewer=reviewer,
            note=note,
            span_index=span_index,
            payload=json.dumps(payload) if payload is not None else None,
        )
        logger.info(
            "Review event recorded job_id=%s event_type=%s span_index=%s reviewer=%s",
            job_id,
            event_type,
            span_index if span_index is not None else "-",
            reviewer or "<unknown>",
        )
        return event

    def list_review_events(self, job_id: str) -> list[ReviewEventModel]:
        return self.reviews.list_for_job(job_id)

    # ------------------------------------------------------------------
    # Manual redactions (reviewer-driven, on top of the auto pipeline)
    # ------------------------------------------------------------------

    def apply_manual_redaction(
        self,
        job_id: str,
        start: int,
        end: int,
        entity_type: str,
        *,
        expected_text: str | None = None,
        reviewer: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Find every occurrence of the selected fragment in the current
        redacted text and replace each one with the same indexed placeholder.

        Why find-and-replace-all instead of point edits at [start:end):
        - The reviewer expects "if it's PII once, it's PII everywhere".
        - It's robust: tiny offset drift between frontend and disk no longer
          breaks the operation. We use the literal text content as truth.
        - The indexed strategy already deduplicates, so all occurrences end
          up as the same ``[PESSOA_NN]``.

        Parameters
        ----------
        start, end:
            Position hint, kept for backwards compatibility. Used as the
            fragment source if ``expected_text`` is not provided.
        expected_text:
            Literal text the reviewer selected. Preferred source of truth.
        """
        job = self.jobs.get(job_id)
        if job is None:
            raise InvalidStateError(f"Job {job_id!r} not found")
        if job.status in (STATUS_PENDING, STATUS_PROCESSING, STATUS_FAILED):
            raise InvalidStateError(
                f"Cannot apply manual redaction to job in status {job.status!r}"
            )
        if not job.redacted_path or not job.spans_path:
            raise InvalidStateError("Job has no artefacts to amend")

        redacted_path = Path(job.redacted_path)
        spans_path = Path(job.spans_path)
        text = redacted_path.read_text(encoding="utf-8")

        # Resolve the fragment: prefer expected_text (sent by the UI from the
        # actual selection), fall back to a slice. Using the literal text
        # avoids spurious failures when the document changed since the
        # selection was made.
        if expected_text:
            fragment = expected_text
        else:
            if start < 0 or end > len(text) or start >= end:
                raise InvalidStateError(
                    f"Invalid range [{start}:{end}) for text of length {len(text)}"
                )
            fragment = text[start:end]

        if not fragment.strip():
            raise InvalidStateError("Selected fragment is empty")

        # Find every non-overlapping occurrence of the fragment in the
        # current redacted text.
        occurrences: list[int] = []
        search_from = 0
        while True:
            pos = text.find(fragment, search_from)
            if pos == -1:
                break
            occurrences.append(pos)
            search_from = pos + len(fragment)

        if not occurrences:
            raise InvalidStateError(
                "Selected text not found in current document. "
                "Reload the page and select again."
            )

        policy = Policy.from_yaml(self.settings.policy_path)
        entity_cfg = policy.get(entity_type)
        if entity_cfg is None:
            raise InvalidStateError(f"Unknown entity_type {entity_type!r}")

        spans: list[dict[str, Any]] = json.loads(
            spans_path.read_text(encoding="utf-8")
        )

        replacement = self._compute_manual_replacement(
            fragment=fragment,
            entity_type=entity_type,
            entity_cfg=entity_cfg,
            existing_spans=spans,
        )

        # Backfill redacted positions for legacy spans before we shift them.
        self._ensure_redacted_positions(spans, original_text=text)

        # Apply find-and-replace in one pass.
        new_text = text.replace(fragment, replacement)
        redacted_path.write_text(new_text, encoding="utf-8")

        delta_per_occ = len(replacement) - len(fragment)

        # Shift each existing span by (number of occurrences before it) * delta.
        for s in spans:
            s_start = s.get("redacted_start")
            s_end = s.get("redacted_end")
            if s_start is None or s_end is None:
                continue
            occurrences_before = sum(
                1 for occ in occurrences if occ + len(fragment) <= s_start
            )
            if occurrences_before:
                shift = occurrences_before * delta_per_occ
                s["redacted_start"] = s_start + shift
                s["redacted_end"] = s_end + shift

        # Add one manual span per occurrence, with positions in NEW text.
        ctx_chars = 50
        fragment_hash = _hash_fragment(fragment)
        for i, occ_old_start in enumerate(occurrences):
            new_occ_start = occ_old_start + i * delta_per_occ
            new_occ_end = new_occ_start + len(replacement)
            ctx_s = max(0, new_occ_start - ctx_chars)
            ctx_e = min(len(new_text), new_occ_end + ctx_chars)
            spans.append(
                {
                    "block_id": "manual",
                    "page": None,
                    "doc_start": -1,
                    "doc_end": -1,
                    "local_start": new_occ_start,
                    "local_end": new_occ_end,
                    "redacted_start": new_occ_start,
                    "redacted_end": new_occ_end,
                    "entity_type": entity_type,
                    "strategy": entity_cfg.strategy,
                    "replacement": replacement,
                    "manual": True,
                    "source": "manual",
                    "confidence": 1.0,
                    "source_fragment_hash": fragment_hash,
                    "original_text": fragment,
                    "original_context_before": new_text[ctx_s:new_occ_start],
                    "original_context_after": new_text[new_occ_end:ctx_e],
                }
            )

        spans_path.write_text(
            json.dumps(spans, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self.reviews.add(
            job_id=job_id,
            event_type="manual_redaction",
            reviewer=reviewer,
            note=note,
            payload=json.dumps(
                {
                    "fragment_hash": fragment_hash,
                    "entity_type": entity_type,
                    "replacement": replacement,
                    "occurrences": len(occurrences),
                }
            ),
        )

        logger.info(
            "Manual redaction job_id=%s entity_type=%s occurrences=%d reviewer=%s",
            job_id,
            entity_type,
            len(occurrences),
            reviewer or "<unknown>",
        )

        report = json.loads(Path(job.report_path).read_text(encoding="utf-8"))
        report["redacted_text"] = new_text
        report["applied_spans"] = spans
        report["manual_redaction_occurrences"] = len(occurrences)
        return report

    def revert_span(
        self,
        job_id: str,
        span_index: int,
        *,
        reviewer: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Revert a span: put the original text back at its position in the
        redacted file and mark the span as a false positive.

        Records a ReviewEvent (event_type=false_positive) and shifts every
        subsequent span's redacted positions by the length delta so the
        rest of the highlights stay aligned.
        """
        job = self.jobs.get(job_id)
        if job is None:
            raise InvalidStateError(f"Job {job_id!r} not found")
        if job.status in (STATUS_PENDING, STATUS_PROCESSING, STATUS_FAILED):
            raise InvalidStateError(
                f"Cannot revert span in status {job.status!r}"
            )
        if not job.redacted_path or not job.spans_path:
            raise InvalidStateError("Job has no artefacts to amend")

        redacted_path = Path(job.redacted_path)
        spans_path = Path(job.spans_path)
        text = redacted_path.read_text(encoding="utf-8")
        spans: list[dict[str, Any]] = json.loads(
            spans_path.read_text(encoding="utf-8")
        )

        if not (0 <= span_index < len(spans)):
            raise InvalidStateError(
                f"Invalid span_index {span_index} (have {len(spans)} spans)"
            )

        span = spans[span_index]
        if span.get("false_positive"):
            raise InvalidStateError(
                f"Span {span_index} is already marked as false positive"
            )

        original = span.get("original_text")
        if not original:
            raise InvalidStateError(
                "Span has no original_text — reprocess the document so the "
                "pipeline records the original PII before revert is possible."
            )

        # Position math depends on the authoritative redacted offsets.
        self._ensure_redacted_positions(spans, original_text=text)
        rstart = span["redacted_start"]
        rend = span["redacted_end"]

        # Restore the original
        new_text = text[:rstart] + original + text[rend:]
        redacted_path.write_text(new_text, encoding="utf-8")

        # Shift positions of all spans whose start is at or beyond the
        # reverted range. The reverted span itself is updated separately.
        delta = len(original) - (rend - rstart)
        if delta != 0:
            for i, s in enumerate(spans):
                if i == span_index:
                    continue
                s_start = s.get("redacted_start")
                s_end = s.get("redacted_end")
                if s_start is None or s_end is None:
                    continue
                if s_start >= rend:
                    s["redacted_start"] = s_start + delta
                    s["redacted_end"] = s_end + delta

        # Update the reverted span itself
        span["false_positive"] = True
        span["original_replacement"] = span.get("replacement")
        span["redacted_end"] = rstart + len(original)
        spans[span_index] = span

        spans_path.write_text(
            json.dumps(spans, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self.reviews.add(
            job_id=job_id,
            event_type="false_positive",
            reviewer=reviewer,
            note=note,
            span_index=span_index,
        )

        logger.info(
            "Span reverted job_id=%s span_index=%d reviewer=%s",
            job_id,
            span_index,
            reviewer or "<unknown>",
        )

        report = json.loads(Path(job.report_path).read_text(encoding="utf-8"))
        report["redacted_text"] = new_text
        report["applied_spans"] = spans
        return report

    @staticmethod
    def _ensure_redacted_positions(
        spans: list[dict[str, Any]], *, original_text: str
    ) -> None:
        """Backfill redacted_start/redacted_end on spans that don't have them.

        For auto spans we re-derive positions from doc_start via delta math.
        For manual spans we trust local_start/local_end (set at insert time).
        Mutates ``spans`` in place. ``original_text`` is the **current**
        redacted text — used only for sanity (not consumed here).
        """
        if all("redacted_start" in s for s in spans):
            return

        auto = [s for s in spans if not s.get("manual") and s.get("doc_start", -1) >= 0]
        auto_sorted = sorted(auto, key=lambda s: s["doc_start"])
        delta = 0
        for s in auto_sorted:
            rstart = s["doc_start"] + delta
            s["redacted_start"] = rstart
            s["redacted_end"] = rstart + len(s["replacement"])
            delta += len(s["replacement"]) - (s["doc_end"] - s["doc_start"])

        for s in spans:
            if s.get("manual") and "redacted_start" not in s:
                s["redacted_start"] = s.get("local_start", 0)
                s["redacted_end"] = s.get(
                    "local_end", s["redacted_start"] + len(s["replacement"])
                )

    @staticmethod
    def _compute_manual_replacement(
        *,
        fragment: str,
        entity_type: str,
        entity_cfg: EntityPolicy,
        existing_spans: list[dict[str, Any]],
    ) -> str:
        """Decide what to substitute the manual selection with.

        For ``indexed`` strategy: reuse the same placeholder if the same
        fragment was already manually redacted (dedupe), otherwise allocate
        the next available index for that entity type.
        """
        strategy = entity_cfg.strategy
        if strategy == "indexed":
            fragment_hash = _hash_fragment(fragment)
            # 1. Dedupe: same manual fragment → same placeholder
            for s in existing_spans:
                if (
                    s.get("manual")
                    and s.get("entity_type") == entity_type
                    and s.get("source_fragment_hash") == fragment_hash
                ):
                    return s["replacement"]
            # 2. Find next index by scanning existing replacements
            label = entity_cfg.label
            label_inner = label.strip("[]")
            pattern = re.compile(rf"^\[{re.escape(label_inner)}_(\d+)\]$")
            max_idx = 0
            for s in existing_spans:
                if s.get("entity_type") == entity_type:
                    m = pattern.match(s.get("replacement", "") or "")
                    if m:
                        max_idx = max(max_idx, int(m.group(1)))
            next_idx = max_idx + 1
            if label.endswith("]"):
                return f"{label[:-1]}_{next_idx:02d}]"
            return f"{label}_{next_idx:02d}"
        if strategy == "replace":
            return entity_cfg.label
        if strategy == "mask":
            return entity_cfg.mask_char * len(fragment)
        if strategy == "suppress":
            return ""
        if strategy == "pseudonym":
            return _pseudonym_for(fragment, entity_type)
        return entity_cfg.label

    # ------------------------------------------------------------------
    # Reversible pseudonymization workflow
    # ------------------------------------------------------------------

    _PLACEHOLDER_PATTERN = re.compile(r"\[[A-Z_]+_\d+\]")

    def _require_reversible(self, job_id: str) -> tuple[JobModel, str, list[dict[str, Any]]]:
        """Common pre-conditions for reversible operations.

        Returns (job, redacted_text, spans). Raises InvalidStateError on
        wrong mode/state/missing artefacts.
        """
        job = self.jobs.get(job_id)
        if job is None:
            raise InvalidStateError(f"Job {job_id!r} not found")
        if (job.mode or "anonymization") != "reversible_pseudonymization":
            raise InvalidStateError(
                "This job was processed in irreversible anonymization mode. "
                "Reversible operations are only available for jobs uploaded "
                "with mode=reversible_pseudonymization."
            )
        if job.status in (STATUS_PENDING, STATUS_PROCESSING, STATUS_FAILED):
            raise InvalidStateError(
                f"Cannot run reversible operation in status {job.status!r}"
            )
        if not job.redacted_path or not job.spans_path:
            raise InvalidStateError("Job has no artefacts to package")
        text = Path(job.redacted_path).read_text(encoding="utf-8")
        spans = json.loads(Path(job.spans_path).read_text(encoding="utf-8"))
        return job, text, spans

    @staticmethod
    def _unique_placeholders(
        spans: list[dict[str, Any]], pseudonymized_text: str
    ) -> list[dict[str, Any]]:
        """Group spans by their replacement string and count occurrences.

        Spans marked as ``false_positive`` are skipped — their original text
        is already back in the document, there is no placeholder to restore.
        """
        unique: dict[str, dict[str, Any]] = {}
        for s in spans:
            if s.get("false_positive"):
                continue
            placeholder = s.get("replacement") or ""
            original = s.get("original_text") or ""
            if not placeholder or not original:
                continue
            if placeholder not in unique:
                unique[placeholder] = {
                    "placeholder": placeholder,
                    "original_text": original,
                    "entity_type": s.get("entity_type", "unknown"),
                    "occurrences": pseudonymized_text.count(placeholder),
                }
        return list(unique.values())

    def build_reversible_package(self, job_id: str) -> dict[str, Any]:
        """Return the package the user hands off to the external system."""
        _job, text, spans = self._require_reversible(job_id)
        placeholders = self._unique_placeholders(spans, text)
        return {
            "pseudonymized_text": text,
            "instructions": _REVERSIBLE_INSTRUCTIONS,
            "placeholders": placeholders,
        }

    def validate_processed_text(
        self, job_id: str, processed_text: str
    ) -> dict[str, Any]:
        """Compare placeholders in *processed_text* against what was sent.

        Returns missing / duplicated / unexpected lists.
        """
        _job, text, spans = self._require_reversible(job_id)
        return self._validate(text, spans, processed_text)

    @classmethod
    def _validate(
        cls,
        pseudonymized_text: str,
        spans: list[dict[str, Any]],
        processed_text: str,
    ) -> dict[str, Any]:
        placeholders = cls._unique_placeholders(spans, pseudonymized_text)
        expected_set = {p["placeholder"] for p in placeholders}

        missing: list[dict[str, Any]] = []
        duplicated: list[dict[str, Any]] = []
        for p in placeholders:
            expected = p["occurrences"]
            actual = processed_text.count(p["placeholder"])
            if actual < expected:
                missing.append(
                    {"placeholder": p["placeholder"], "expected": expected, "actual": actual}
                )
            elif actual > expected:
                duplicated.append(
                    {"placeholder": p["placeholder"], "expected": expected, "actual": actual}
                )

        found = set(cls._PLACEHOLDER_PATTERN.findall(processed_text))
        unexpected = sorted(found - expected_set)

        return {
            "valid": not missing and not duplicated and not unexpected,
            "missing": missing,
            "duplicated": duplicated,
            "unexpected": unexpected,
        }

    def restore_processed_text(
        self,
        job_id: str,
        processed_text: str,
        *,
        reviewer: str | None = None,
    ) -> dict[str, Any]:
        """Replace each placeholder in *processed_text* with its original
        value. Persists the result to ``restored.txt`` and returns the
        validation report next to the restored text.
        """
        job, text, spans = self._require_reversible(job_id)
        validation = self._validate(text, spans, processed_text)

        placeholders = self._unique_placeholders(spans, text)
        restored = processed_text
        # Sort by length desc so longer placeholders are replaced first
        # (no actual collision because of brackets, but defensive).
        for p in sorted(placeholders, key=lambda x: -len(x["placeholder"])):
            restored = restored.replace(p["placeholder"], p["original_text"])

        # Persist restored.txt next to the other artefacts.
        output_dir = self.storage.output_for(job_id)
        restored_path = output_dir / "restored.txt"
        restored_path.write_text(restored, encoding="utf-8")
        self.jobs.update(job_id, restored_path=str(restored_path))

        self.reviews.add(
            job_id=job_id,
            event_type="reversible_restore",
            reviewer=reviewer,
            note=None,
            payload=json.dumps(
                {
                    "valid": validation["valid"],
                    "missing": len(validation["missing"]),
                    "duplicated": len(validation["duplicated"]),
                    "unexpected": len(validation["unexpected"]),
                    "placeholders_replaced": len(placeholders),
                }
            ),
        )

        logger.info(
            "Reversible restore job_id=%s placeholders=%d valid=%s reviewer=%s",
            job_id,
            len(placeholders),
            validation["valid"],
            reviewer or "<unknown>",
        )

        return {"restored_text": restored, "validation": validation}

    def reversible_status(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise InvalidStateError(f"Job {job_id!r} not found")
        mode = job.mode or "anonymization"
        is_reversible = mode == "reversible_pseudonymization"
        artefacts_ready = (
            job.status not in (STATUS_PENDING, STATUS_PROCESSING, STATUS_FAILED)
            and job.redacted_path is not None
        )

        placeholder_count = 0
        if is_reversible and artefacts_ready and job.spans_path:
            try:
                spans = json.loads(
                    Path(job.spans_path).read_text(encoding="utf-8")
                )
                text = Path(job.redacted_path).read_text(encoding="utf-8")
                placeholder_count = len(self._unique_placeholders(spans, text))
            except Exception:  # noqa: BLE001
                placeholder_count = 0

        has_restored = bool(
            job.restored_path and Path(job.restored_path).exists()
        )
        return {
            "mode": mode,
            "available": is_reversible and artefacts_ready,
            "has_restored": has_restored,
            "placeholder_count": placeholder_count,
        }

    # ------------------------------------------------------------------
    # Permanent deletion
    # ------------------------------------------------------------------

    def delete(self, job_id: str) -> None:
        """Permanently remove a job: DB rows + quarantined upload + outputs.

        Refuses to delete jobs that are still being processed (race condition
        with the background worker would leave orphaned spans).
        """
        job = self.jobs.get(job_id)
        if job is None:
            raise InvalidStateError(f"Job {job_id!r} not found")
        if job.status in (STATUS_PENDING, STATUS_PROCESSING):
            raise InvalidStateError(
                f"Cannot delete job in status {job.status!r}; "
                "wait for processing to finish"
            )

        quarantine_path = Path(job.quarantine_path) if job.quarantine_path else None
        output_dir = self.storage.output_for(job_id)

        # 1. Drop dependent rows first (no ON DELETE CASCADE on SQLite by default)
        self.spans.delete_for_job(job_id)
        self.reviews.delete_for_job(job_id)

        # 2. Drop the job itself
        self.jobs.delete(job_id)

        # 3. Filesystem cleanup — best-effort, log without leaking content
        if quarantine_path and quarantine_path.exists():
            try:
                quarantine_path.unlink()
            except OSError as exc:
                logger.warning(
                    "Failed to delete quarantine file job_id=%s: %s",
                    job_id, exc,
                )
        if output_dir.exists():
            try:
                shutil.rmtree(output_dir)
            except OSError as exc:
                logger.warning(
                    "Failed to delete output dir job_id=%s: %s",
                    job_id, exc,
                )

        logger.info("Job permanently deleted job_id=%s", job_id)

    # ------------------------------------------------------------------
    # Detector comparison (diagnostic mode — does not change job.status)
    # ------------------------------------------------------------------

    DETECTOR_COMPARISON_FILENAME = "detector_comparison.json"

    def detector_comparison_path(self, job_id: str) -> Path:
        return (
            self.storage.output_for(job_id) / self.DETECTOR_COMPARISON_FILENAME
        )

    def run_detector_comparison(
        self,
        job_id: str,
        *,
        opf_client: PrivacyFilterClient,
        regex_client: PrivacyFilterClient,
    ) -> DetectorComparisonReport:
        """Re-extract the source document and run two detectors side by side.

        * ``opf_client`` is the *base* OPF detector — never the augmented
          composite. The diagnostic exists precisely to show what OPF
          contributes by itself.
        * ``regex_client`` is a ``RegexOnlyClient`` (built once at app
          construction). It owns the deterministic Brazilian regex stack.

        The job's ``status``, ``decision``, ``risk_level`` and existing
        artefacts are **not** touched. The report is persisted next to
        the job's outputs and returned. Logs carry only metadata
        (block counts, item counts, ratios).
        """
        job = self.jobs.get(job_id)
        if job is None:
            raise InvalidStateError(f"Job {job_id!r} not found")
        if job.status in (STATUS_PENDING, STATUS_PROCESSING):
            raise InvalidStateError(
                f"Cannot run detector comparison while job is in status "
                f"{job.status!r}"
            )
        if not job.quarantine_path:
            raise InvalidStateError("Job has no source document on disk")
        source_path = Path(job.quarantine_path)
        if not source_path.exists():
            raise InvalidStateError("Source document missing on disk")

        try:
            extraction = extract_document(source_path)
        except UnsupportedFormatError as exc:
            raise InvalidStateError(str(exc))

        all_items = []
        comparison_blocks: list[ComparisonBlock] = []
        for block in extraction.blocks:
            comparison_blocks.append(
                ComparisonBlock(block_id=block.block_id, text=block.text)
            )
            opf_spans = opf_client.detect(block.text)
            regex_spans = regex_client.detect(block.text)
            items = compare_spans(
                opf_spans=opf_spans,
                regex_spans=regex_spans,
                block_id=block.block_id,
                text=block.text,
            )
            all_items.extend(items)

        report = build_comparison_report(
            job_id=job_id,
            block_results=all_items,
            blocks=comparison_blocks,
        )

        out_path = self.detector_comparison_path(job_id)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(_report_to_dict(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        logger.info(
            "Detector comparison run job_id=%s blocks=%d items=%d",
            job_id,
            len(extraction.blocks),
            report.summary.total,
        )
        return report

    def load_detector_comparison(self, job_id: str) -> dict[str, Any] | None:
        """Return the persisted comparison payload as a dict, or None if
        the comparison was never run for this job."""
        if self.jobs.get(job_id) is None:
            raise InvalidStateError(f"Job {job_id!r} not found")
        path = self.detector_comparison_path(job_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


def _report_to_dict(report: DetectorComparisonReport) -> dict[str, Any]:
    """Manual dataclass-to-dict conversion to keep the structure explicit
    (``dataclasses.asdict`` would also work, but this keeps key order
    stable across Python versions and makes the JSON shape obvious)."""
    return {
        "job_id": report.job_id,
        "summary": _summary_to_dict(report.summary),
        "by_entity_type": [
            {
                "entity_type": ec.entity_type,
                "summary": _summary_to_dict(ec.summary),
            }
            for ec in report.by_entity_type
        ],
        "items": [_item_to_dict(it) for it in report.items],
        "blocks": [
            {"block_id": b.block_id, "text": b.text} for b in report.blocks
        ],
    }


def _summary_to_dict(summary: Any) -> dict[str, int]:
    return {
        "total": summary.total,
        "both": summary.both,
        "opf_only": summary.opf_only,
        "regex_only": summary.regex_only,
        "partial_overlap": summary.partial_overlap,
        "type_conflict": summary.type_conflict,
    }


def _item_to_dict(item: Any) -> dict[str, Any]:
    return {
        "block_id": item.block_id,
        "status": item.status,
        "opf_span": _span_to_dict(item.opf_span),
        "regex_span": _span_to_dict(item.regex_span),
        "overlap_ratio": item.overlap_ratio,
        "context_preview": item.context_preview,
    }


def _span_to_dict(span: Any) -> dict[str, Any] | None:
    if span is None:
        return None
    return {
        "start": span.start,
        "end": span.end,
        "entity_type": span.entity_type,
        "confidence": span.confidence,
        "source": span.source,
        "text_preview": span.text_preview,
    }
