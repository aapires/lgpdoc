"""HTTP routes for pseudonymization containers.

The endpoints live under ``/api/containers`` and are deliberately
isolated from the ``/jobs`` router to keep this feature from
contaminating the existing three modes' surface area.
"""
from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from ..containers.schemas import (
    ContainerCreate,
    ContainerDocumentSummary,
    ContainerMappingEntryView,
    ContainerMappingOccurrence,
    ContainerRestoreResult,
    ContainerSummary,
    ContainerUpdate,
    ContainerValidationSummary,
    PseudonymizedManualRedactionRequest,
    PseudonymizedManualRedactionResult,
    PseudonymizedReviewPayload,
    RestoreTextRequest,
    ValidatePseudonymizedRequest,
)
from ..containers.service import (
    ContainerDocumentNotFoundError,
    ContainerNotFoundError,
    ContainerService,
    ContainerValidationError,
)
from ..deps import get_container_service

_XLSX_MIMETYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/containers", tags=["containers"])


def _to_summary(
    service: ContainerService, obj
) -> ContainerSummary:
    """Build a ``ContainerSummary`` filling the aggregate counts.

    Counts are zero in Sprint 1 — the helper exists so Sprint 2 can wire
    real numbers in one place rather than threading them through every
    endpoint.
    """
    return ContainerSummary(
        container_id=obj.container_id,
        name=obj.name,
        description=obj.description,
        status=obj.status,
        document_count=service.count_documents(obj.container_id),
        marker_count=service.count_markers(obj.container_id),
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=ContainerSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_container(
    body: ContainerCreate,
    service: ContainerService = Depends(get_container_service),
) -> ContainerSummary:
    try:
        obj = service.create(name=body.name, description=body.description)
    except ContainerValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    return _to_summary(service, obj)


@router.get("", response_model=list[ContainerSummary])
def list_containers(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status"),
    service: ContainerService = Depends(get_container_service),
) -> list[ContainerSummary]:
    try:
        rows = service.list(limit=limit, offset=offset, status=status_filter)
    except ContainerValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    return [_to_summary(service, obj) for obj in rows]


@router.get(
    "/{container_id}",
    response_model=ContainerSummary,
)
def get_container(
    container_id: str,
    service: ContainerService = Depends(get_container_service),
) -> ContainerSummary:
    try:
        obj = service.get(container_id)
    except ContainerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    return _to_summary(service, obj)


@router.patch(
    "/{container_id}",
    response_model=ContainerSummary,
)
def update_container(
    container_id: str,
    body: ContainerUpdate,
    service: ContainerService = Depends(get_container_service),
) -> ContainerSummary:
    try:
        obj = service.update(
            container_id,
            name=body.name,
            description=body.description,
            status=body.status,
        )
    except ContainerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except ContainerValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    return _to_summary(service, obj)


@router.delete(
    "/{container_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_container(
    container_id: str,
    service: ContainerService = Depends(get_container_service),
) -> None:
    try:
        service.delete(container_id)
    except ContainerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@router.get(
    "/{container_id}/documents",
    response_model=list[ContainerDocumentSummary],
)
def list_documents(
    container_id: str,
    service: ContainerService = Depends(get_container_service),
) -> list[ContainerDocumentSummary]:
    try:
        rows = service.list_documents(container_id)
    except ContainerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    return [ContainerDocumentSummary.model_validate(d) for d in rows]


@router.get(
    "/{container_id}/documents/{document_id}",
    response_model=ContainerDocumentSummary,
)
def get_document(
    container_id: str,
    document_id: str,
    service: ContainerService = Depends(get_container_service),
) -> ContainerDocumentSummary:
    try:
        doc = service.get_document(container_id, document_id)
    except (ContainerNotFoundError, ContainerDocumentNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    return ContainerDocumentSummary.model_validate(doc)


@router.post(
    "/{container_id}/documents/raw",
    response_model=ContainerDocumentSummary,
    status_code=status.HTTP_201_CREATED,
)
async def upload_raw_document(
    request: Request,
    container_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    role: str = Form("source"),
    service: ContainerService = Depends(get_container_service),
) -> ContainerDocumentSummary:
    """Submit a raw sensitive document to the container's review
    queue. The pipeline (extract → detect → redact) runs in the
    background; the document lands in ``processing`` status, then the
    post-processing hook flips it to ``pending_review`` so the user
    can open it in ``/jobs/{job_id}/review`` and act."""
    if file.filename is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename"
        )
    content = await file.read()
    # The factory wires the existing JobService — kept at the router
    # layer so ``containers/`` does not import from ``jobs/``.
    job_factory = request.app.state.service_factory
    try:
        doc = service.add_raw_document(
            container_id,
            filename=file.filename,
            content=content,
            role=role,
            job_factory=job_factory,
        )
    except ContainerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except ContainerValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )

    # Kick the background pipeline. The hook in JobService.process
    # will, on completion, transition the ContainerDocument from
    # ``processing`` to ``pending_review`` (or ``failed``).
    if doc.job_id:
        from .jobs import _run_processing
        background_tasks.add_task(_run_processing, request.app, doc.job_id)
    return ContainerDocumentSummary.model_validate(doc)


@router.delete(
    "/{container_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_document(
    container_id: str,
    document_id: str,
    service: ContainerService = Depends(get_container_service),
) -> None:
    try:
        service.delete_document(container_id, document_id)
    except (ContainerNotFoundError, ContainerDocumentNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )


# ---------------------------------------------------------------------------
# Pseudonymised artefact downloads (single + bundle)
# ---------------------------------------------------------------------------

@router.get(
    "/{container_id}/documents/{document_id}/download",
)
def download_document(
    container_id: str,
    document_id: str,
    service: ContainerService = Depends(get_container_service),
) -> Response:
    """Download the pseudonymised text of a single ``ready`` document.

    The payload contains marker tokens like ``[PESSOA_0001]`` rather
    than real values — sharing it does not reidentify anyone *unless*
    paired with the container's mapping table."""
    try:
        doc = service.get_document(container_id, document_id)
    except (ContainerNotFoundError, ContainerDocumentNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    # Both presence of the artefact AND the ``ready`` status must hold:
    # pseudonymized uploads write the artefact immediately but stay in
    # ``pending_review`` until the operator approves.
    if not doc.pseudonymized_path or doc.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Document not ready for download (status={doc.status!r}). "
                "Approve the review first."
            ),
        )
    try:
        text = service.read_pseudonymized_text(container_id, document_id)
    except ContainerValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    filename = service.download_filename_for(doc)
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


@router.get(
    "/{container_id}/download-bundle.zip",
)
def download_bundle(
    container_id: str,
    service: ContainerService = Depends(get_container_service),
) -> Response:
    """ZIP archive bundling every ``ready`` document's pseudonymised
    text. Documents still in review or rejected are skipped."""
    try:
        payload, count = service.build_pseudonymized_bundle(container_id)
    except ContainerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    if count == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Nenhum documento pronto para download neste container. "
                "Aprove ao menos uma revisão antes de baixar o pacote."
            ),
        )
    filename = f"container-{container_id[:8]}.zip"
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


# ---------------------------------------------------------------------------
# Conversion table (mapping)
# ---------------------------------------------------------------------------

@router.get(
    "/{container_id}/mapping",
    response_model=list[ContainerMappingEntryView],
)
def list_mapping(
    container_id: str,
    service: ContainerService = Depends(get_container_service),
) -> list[ContainerMappingEntryView]:
    """Conversion table — INCLUDES ``original_text``. Always sensitive
    output (don't cache, don't log). The XLSX ``export-sensitive``
    endpoint serves the same payload as a downloadable spreadsheet.

    Each entry carries an ``occurrences`` list with the documents where
    the marker was observed. Sourced from ``ContainerSpan`` rows; falls
    back to the entry's ``created_from_document_id`` when no spans are
    recorded.
    """
    try:
        rows = service.list_mapping(container_id)
    except ContainerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    occurrences = service.mapping.list_occurrences_by_entry(
        container_id, [r.id for r in rows]
    )
    out: list[ContainerMappingEntryView] = []
    for r in rows:
        view = ContainerMappingEntryView.model_validate(r)
        view.occurrences = [
            ContainerMappingOccurrence(document_id=doc_id, filename=filename)
            for doc_id, filename in occurrences.get(r.id, [])
        ]
        out.append(view)
    return out


@router.get(
    "/{container_id}/mapping/export-sensitive.xlsx",
)
def export_mapping_sensitive(
    container_id: str,
    service: ContainerService = Depends(get_container_service),
) -> Response:
    """Sensitive XLSX — INCLUDES ``original_text``. Treat the response
    body as the mapping table itself: storing it elsewhere effectively
    duplicates the container's secret."""
    try:
        payload = service.export_mapping_sensitive(container_id)
    except ContainerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    filename = f"mapping-sensitive-{container_id[:8]}.xlsx"
    return Response(
        content=payload,
        media_type=_XLSX_MIMETYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Already-pseudonymized documents (Sprint 3)
# ---------------------------------------------------------------------------

@router.post(
    "/{container_id}/documents/pseudonymized",
    response_model=ContainerDocumentSummary,
    status_code=status.HTTP_201_CREATED,
)
async def upload_pseudonymized_document(
    container_id: str,
    file: UploadFile = File(...),
    role: str = Form("edited_version"),
    service: ContainerService = Depends(get_container_service),
) -> ContainerDocumentSummary:
    """Import a derived document that's already been pseudonymized
    elsewhere. Markers in the file are validated against the
    container's mapping table; new mapping entries are NEVER created
    by this flow."""
    if file.filename is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename"
        )
    content = await file.read()
    try:
        doc = service.add_pseudonymized_document(
            container_id,
            filename=file.filename,
            content=content,
            role=role,
        )
    except ContainerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except ContainerValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    return ContainerDocumentSummary.model_validate(doc)


# ---------------------------------------------------------------------------
# Pseudonymized review — dedicated screen for already-pseudonymized docs
# ---------------------------------------------------------------------------

@router.get(
    "/{container_id}/documents/{document_id}/review-pseudonymized",
    response_model=PseudonymizedReviewPayload,
)
def get_pseudonymized_review(
    container_id: str,
    document_id: str,
    service: ContainerService = Depends(get_container_service),
) -> PseudonymizedReviewPayload:
    """Build the review payload: text + validation summary + residual
    PII flagged by the augmented detector."""
    try:
        payload = service.build_pseudonymized_review_payload(
            container_id, document_id
        )
    except (ContainerNotFoundError, ContainerDocumentNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except ContainerValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    return PseudonymizedReviewPayload(**payload)


@router.post(
    "/{container_id}/documents/{document_id}/approve-pseudonymized",
    response_model=ContainerDocumentSummary,
)
def approve_pseudonymized_document(
    container_id: str,
    document_id: str,
    service: ContainerService = Depends(get_container_service),
) -> ContainerDocumentSummary:
    try:
        doc = service.approve_pseudonymized_document(
            container_id, document_id
        )
    except (ContainerNotFoundError, ContainerDocumentNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except ContainerValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    return ContainerDocumentSummary.model_validate(doc)


@router.post(
    "/{container_id}/documents/{document_id}/reject-pseudonymized",
    response_model=ContainerDocumentSummary,
)
def reject_pseudonymized_document(
    container_id: str,
    document_id: str,
    service: ContainerService = Depends(get_container_service),
) -> ContainerDocumentSummary:
    try:
        doc = service.reject_pseudonymized_document(
            container_id, document_id
        )
    except (ContainerNotFoundError, ContainerDocumentNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except ContainerValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    return ContainerDocumentSummary.model_validate(doc)


@router.post(
    "/{container_id}/documents/{document_id}/manual-redaction-pseudonymized",
    response_model=PseudonymizedManualRedactionResult,
)
def manual_redaction_pseudonymized(
    container_id: str,
    document_id: str,
    body: PseudonymizedManualRedactionRequest,
    service: ContainerService = Depends(get_container_service),
) -> PseudonymizedManualRedactionResult:
    """Anonymise residual PII the reviewer found in a pseudonymized
    document. Allocates / reuses a container marker and replaces every
    occurrence of the fragment in the text."""
    try:
        result = service.apply_manual_redaction_to_pseudonymized(
            container_id,
            document_id,
            fragment=body.fragment,
            entity_type=body.entity_type,
        )
    except (ContainerNotFoundError, ContainerDocumentNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except ContainerValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    return PseudonymizedManualRedactionResult(**result)


@router.post(
    "/{container_id}/restore/text",
    response_model=ContainerRestoreResult,
)
def restore_text_endpoint(
    container_id: str,
    body: RestoreTextRequest,
    service: ContainerService = Depends(get_container_service),
) -> ContainerRestoreResult:
    """Restore originals in arbitrary pseudonymized text. The
    ``container_id`` filter is the only safe scope — markers from
    other containers are reported as unknown, never replaced."""
    try:
        summary = service.restore_text(container_id, body.processed_text)
    except ContainerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    return ContainerRestoreResult(**summary.to_dict())


@router.post(
    "/{container_id}/restore/document/{document_id}",
    response_model=ContainerRestoreResult,
)
def restore_document_endpoint(
    container_id: str,
    document_id: str,
    service: ContainerService = Depends(get_container_service),
) -> ContainerRestoreResult:
    """Restore originals in a previously-processed document of the
    container, using the container's mapping table."""
    try:
        summary = service.restore_document(container_id, document_id)
    except (ContainerNotFoundError, ContainerDocumentNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except ContainerValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    return ContainerRestoreResult(**summary.to_dict())


@router.post(
    "/{container_id}/validate-pseudonymized",
    response_model=ContainerValidationSummary,
)
def validate_pseudonymized(
    container_id: str,
    body: ValidatePseudonymizedRequest,
    service: ContainerService = Depends(get_container_service),
) -> ContainerValidationSummary:
    """Validate an arbitrary pseudonymized text against the container's
    mapping. Stateless — no document is created. Useful for the
    "paste-and-check" UI."""
    try:
        summary = service.validate_pseudonymized_text(
            container_id, body.processed_text
        )
    except ContainerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    return ContainerValidationSummary(
        total_well_formed=summary.total_well_formed,
        known_markers=summary.known_markers,
        unknown_markers=summary.unknown_markers,
        malformed_markers=summary.malformed_markers,
        is_clean=summary.is_clean,
    )
