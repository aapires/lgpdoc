"""Job-related endpoints: upload, status, report, download, approve, reject."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse

from ..deps import get_service
from ..jobs.service import (
    InvalidStateError,
    JobService,
    UploadValidationError,
)
from ..schemas import (
    JobStatus,
    ManualRedactionRequest,
    ProcessedTextRequest,
    RestoredResult,
    ReversiblePackage,
    ReversibleStatus,
    ReviewEventRequest,
    ReviewEventResponse,
    ReviewRequest,
    UploadResponse,
    ValidationReport,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# Background task entrypoint
# ---------------------------------------------------------------------------

def _run_processing(app, job_id: str) -> None:
    """Open a fresh DB session and run the pipeline for *job_id*.

    Background tasks must not reuse the request-scoped session — it is
    closed by the dependency teardown before this code runs.
    """
    db = app.state.database.session()
    try:
        service: JobService = app.state.service_factory(db)
        service.process(job_id)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_job(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    mode: str = Form("anonymization"),
    service: JobService = Depends(get_service),
) -> UploadResponse:
    if file.filename is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename"
        )

    content = await file.read()
    try:
        job = service.submit_upload(file.filename, content, mode=mode)
    except UploadValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )

    background_tasks.add_task(_run_processing, request.app, job.job_id)
    return UploadResponse(
        job_id=job.job_id, status=job.status, created_at=job.created_at
    )


@router.get("", response_model=list[JobStatus])
def list_jobs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status"),
    service: JobService = Depends(get_service),
) -> list[JobStatus]:
    jobs = service.list_jobs(limit=limit, offset=offset, status=status_filter)
    return [JobStatus.model_validate(j) for j in jobs]


@router.get("/{job_id}", response_model=JobStatus)
def get_job(job_id: str, service: JobService = Depends(get_service)) -> JobStatus:
    job = service.jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    return JobStatus.model_validate(job)


@router.get("/{job_id}/report")
def get_report(job_id: str, service: JobService = Depends(get_service)) -> JSONResponse:
    """Return the full review payload: verification + redacted text + applied spans.

    Showing redacted text is safe — it's the post-redaction artefact, not the
    original document. The actual download endpoint remains gated on final
    approval.
    """
    job = service.jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    if not job.report_path or not Path(job.report_path).exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Verification report not available (status={job.status!r})",
        )

    report = json.loads(Path(job.report_path).read_text(encoding="utf-8"))
    if job.redacted_path and Path(job.redacted_path).exists():
        report["redacted_text"] = Path(job.redacted_path).read_text(encoding="utf-8")
    if job.spans_path and Path(job.spans_path).exists():
        report["applied_spans"] = json.loads(
            Path(job.spans_path).read_text(encoding="utf-8")
        )
    return JSONResponse(content=report)


@router.get("/{job_id}/download")
def download_redacted(
    job_id: str, service: JobService = Depends(get_service)
) -> FileResponse:
    job = service.jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    if not service.can_download(job):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Download not allowed for job in status {job.status!r} "
                f"(decision={job.decision!r})"
            ),
        )
    if not job.redacted_path or not Path(job.redacted_path).exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Redacted artefact missing on disk",
        )
    logger.info("Download served job_id=%s status=%s", job_id, job.status)
    return FileResponse(
        job.redacted_path,
        media_type="text/plain",
        filename=f"{job_id}-redacted.txt",
    )


@router.post("/{job_id}/approve", response_model=JobStatus)
def approve_job(
    job_id: str,
    body: ReviewRequest,
    service: JobService = Depends(get_service),
) -> JobStatus:
    try:
        job = service.approve(job_id, body.reviewer, body.note)
    except InvalidStateError as exc:
        # 404 if not found, 409 if state conflict
        msg = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in msg
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=msg)
    return JobStatus.model_validate(job)


@router.post("/{job_id}/reject", response_model=JobStatus)
def reject_job(
    job_id: str,
    body: ReviewRequest,
    service: JobService = Depends(get_service),
) -> JobStatus:
    try:
        job = service.reject(job_id, body.reviewer, body.note)
    except InvalidStateError as exc:
        msg = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in msg
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=msg)
    return JobStatus.model_validate(job)


@router.post("/{job_id}/unapprove", response_model=JobStatus)
def unapprove_job(
    job_id: str,
    body: ReviewRequest,
    service: JobService = Depends(get_service),
) -> JobStatus:
    """Undo a previous approval — moves the job back to awaiting_review."""
    try:
        job = service.revert_approval(job_id, body.reviewer, body.note)
    except InvalidStateError as exc:
        msg = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in msg
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=msg)
    return JobStatus.model_validate(job)


@router.post(
    "/{job_id}/review-events",
    response_model=ReviewEventResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_review_event(
    job_id: str,
    body: ReviewEventRequest,
    service: JobService = Depends(get_service),
) -> ReviewEventResponse:
    """Record a per-span (or doc-level) review event.

    This does NOT change job status. The reviewer ultimately calls
    /approve or /reject to commit a final decision.
    """
    try:
        event = service.record_review_event(
            job_id=job_id,
            event_type=body.event_type,
            reviewer=body.reviewer,
            note=body.note,
            span_index=body.span_index,
            payload=body.payload,
        )
    except InvalidStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    return ReviewEventResponse.model_validate(event)


@router.get("/{job_id}/review-events", response_model=list[ReviewEventResponse])
def list_review_events(
    job_id: str, service: JobService = Depends(get_service)
) -> list[ReviewEventResponse]:
    if service.jobs.get(job_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    events = service.list_review_events(job_id)
    return [ReviewEventResponse.model_validate(e) for e in events]


@router.post("/{job_id}/spans/{span_index}/revert")
def revert_span(
    job_id: str,
    span_index: int,
    body: ReviewRequest,
    service: JobService = Depends(get_service),
) -> JSONResponse:
    """Mark a span as false positive: the original text is restored in place
    and subsequent spans are shifted accordingly. Returns the updated report.
    """
    try:
        report = service.revert_span(
            job_id,
            span_index,
            reviewer=body.reviewer,
            note=body.note,
        )
    except InvalidStateError as exc:
        msg = str(exc)
        if "not found" in msg or "Invalid span_index" in msg:
            code = status.HTTP_404_NOT_FOUND
        elif "already marked" in msg or "no original_text" in msg:
            code = status.HTTP_400_BAD_REQUEST
        else:
            code = status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=msg)
    return JSONResponse(content=report)


@router.post("/{job_id}/manual-redactions")
def apply_manual_redaction(
    job_id: str,
    body: ManualRedactionRequest,
    service: JobService = Depends(get_service),
) -> JSONResponse:
    """Apply a reviewer-selected redaction over the current redacted text.

    Used when the auto pipeline misses PII and the reviewer marks it via the
    UI. Returns the updated report (same shape as GET /jobs/{id}/report).
    """
    try:
        report = service.apply_manual_redaction(
            job_id=job_id,
            start=body.start,
            end=body.end,
            entity_type=body.entity_type,
            expected_text=body.expected_text,
            reviewer=body.reviewer,
            note=body.note,
        )
    except InvalidStateError as exc:
        msg = str(exc)
        if "Job" in msg and "not found" in msg:
            code = status.HTTP_404_NOT_FOUND
        elif (
            "Invalid range" in msg
            or "Unknown entity_type" in msg
            or "not found in current document" in msg
            or "empty" in msg.lower()
        ):
            code = status.HTTP_400_BAD_REQUEST
        else:
            code = status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=msg)
    return JSONResponse(content=report)


def _reversible_error_to_http(exc: InvalidStateError) -> HTTPException:
    msg = str(exc)
    if "not found" in msg:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=msg
        )
    if "irreversible anonymization" in msg or "no artefacts" in msg:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=msg
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail=msg
    )


@router.post(
    "/{job_id}/reversible/package", response_model=ReversiblePackage
)
def reversible_package(
    job_id: str,
    service: JobService = Depends(get_service),
) -> ReversiblePackage:
    """Build the package the user hands off to the external system."""
    try:
        return ReversiblePackage(**service.build_reversible_package(job_id))
    except InvalidStateError as exc:
        raise _reversible_error_to_http(exc)


@router.post(
    "/{job_id}/reversible/validate", response_model=ValidationReport
)
def reversible_validate(
    job_id: str,
    body: ProcessedTextRequest,
    service: JobService = Depends(get_service),
) -> ValidationReport:
    """Check that the processed text still has every expected placeholder."""
    try:
        return ValidationReport(
            **service.validate_processed_text(job_id, body.processed_text)
        )
    except InvalidStateError as exc:
        raise _reversible_error_to_http(exc)


@router.post(
    "/{job_id}/reversible/restore", response_model=RestoredResult
)
def reversible_restore(
    job_id: str,
    body: ProcessedTextRequest,
    service: JobService = Depends(get_service),
) -> RestoredResult:
    """Substitute every placeholder in the processed text by its original
    value and persist the result to ``restored.txt``."""
    try:
        result = service.restore_processed_text(job_id, body.processed_text)
    except InvalidStateError as exc:
        raise _reversible_error_to_http(exc)
    return RestoredResult(
        restored_text=result["restored_text"],
        validation=ValidationReport(**result["validation"]),
    )


@router.get("/{job_id}/reversible/download")
def reversible_download(
    job_id: str, service: JobService = Depends(get_service)
) -> FileResponse:
    """Download the previously restored text. Same gating as the regular
    download — requires an approved/auto-approved job."""
    job = service.jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    if (job.mode or "anonymization") != "reversible_pseudonymization":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job is not in reversible mode",
        )
    if not service.can_download(job):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Download not allowed for job in status {job.status!r}. "
                "Approve the job first."
            ),
        )
    if not job.restored_path or not Path(job.restored_path).exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No restored text yet. Run /reversible/restore first.",
        )
    logger.info("Reversible download served job_id=%s", job_id)
    return FileResponse(
        job.restored_path,
        media_type="text/plain",
        filename=f"{job_id}-restored.txt",
    )


@router.get(
    "/{job_id}/reversible/status", response_model=ReversibleStatus
)
def reversible_status_endpoint(
    job_id: str, service: JobService = Depends(get_service)
) -> ReversibleStatus:
    try:
        return ReversibleStatus(**service.reversible_status(job_id))
    except InvalidStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: str, service: JobService = Depends(get_service)
) -> None:
    """Permanently delete a job and all of its artefacts (DB rows + files).
    Refuses jobs that are still pending or processing (409)."""
    try:
        service.delete(job_id)
    except InvalidStateError as exc:
        msg = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in msg
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=msg)
