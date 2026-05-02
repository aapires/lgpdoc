"""Detector-comparison endpoints — diagnostic OPF vs regex.

Two endpoints, both scoped to an existing job:

* ``POST /jobs/{job_id}/detector-comparison`` — runs the comparison and
  persists the result on disk next to the job's other artefacts.
* ``GET /jobs/{job_id}/detector-comparison`` — returns the most recent
  saved report; 404 if the comparison was never run.

Neither endpoint mutates ``job.status`` or any other field on the job.
The mode is a read-only inspection tool.

The POST endpoint forces OPF to be loaded (``ensure_loaded()``) before
running the diagnostic — comparison without the model side is a
nonsense report. After the call, the toggle stays ON; the user can
disable it later from the header button.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from anonymizer.augmentations import CaseNormalizingClient
from anonymizer.client import PrivacyFilterClient
from anonymizer.subprocess_opf_client import OPFWorkerError

from ..deps import get_service
from ..jobs.service import (
    InvalidStateError,
    JobService,
    _report_to_dict,
)
from ..opf_manager import OPFManager
from ..schemas import DetectorComparisonReportSchema

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["detector-comparison"])


def _get_regex_client(request: Request) -> PrivacyFilterClient:
    return request.app.state.regex_client


def _get_opf_manager(request: Request) -> OPFManager:
    return request.app.state.opf_manager


@router.post(
    "/{job_id}/detector-comparison",
    response_model=DetectorComparisonReportSchema,
)
def run_detector_comparison(
    job_id: str,
    request: Request,
    service: JobService = Depends(get_service),
    regex_client: PrivacyFilterClient = Depends(_get_regex_client),
) -> JSONResponse:
    manager: OPFManager = _get_opf_manager(request)
    if not manager.available:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "OPF não está disponível neste servidor (modo mock). "
                "A comparação precisa do modelo para gerar o lado OPF."
            ),
        )
    try:
        manager.ensure_loaded()
    except OPFWorkerError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Falha ao subir o OPF para a comparação: {exc}",
        )

    leased = manager.acquire()
    try:
        opf_client: PrivacyFilterClient = CaseNormalizingClient(leased)
        try:
            report = service.run_detector_comparison(
                job_id, opf_client=opf_client, regex_client=regex_client
            )
        except InvalidStateError as exc:
            msg = str(exc)
            code = (
                status.HTTP_404_NOT_FOUND
                if "not found" in msg or "missing on disk" in msg
                else status.HTTP_409_CONFLICT
            )
            raise HTTPException(status_code=code, detail=msg)
    finally:
        manager.release(leased)

    logger.info(
        "Detector comparison endpoint POST job_id=%s items=%d",
        job_id,
        report.summary.total,
    )
    return JSONResponse(content=_report_to_dict(report))


@router.get(
    "/{job_id}/detector-comparison",
    response_model=DetectorComparisonReportSchema,
)
def get_detector_comparison(
    job_id: str,
    service: JobService = Depends(get_service),
) -> JSONResponse:
    try:
        payload = service.load_detector_comparison(job_id)
    except InvalidStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Detector comparison not yet generated for this job",
        )
    return JSONResponse(content=payload)
