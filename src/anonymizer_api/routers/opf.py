"""Runtime toggle for the OPF model — endpoints used by the UI button.

The ``OPFManager`` lives in ``app.state.opf_manager`` and owns the
subprocess lifecycle. These endpoints are thin wrappers; all the
state-machine logic lives there so it can be exercised directly in
unit tests without the FastAPI layer.

* ``GET  /api/opf/status``  — current state (available/enabled/loading).
* ``POST /api/opf/enable``  — spawn the OPF subprocess (blocks until ready).
* ``POST /api/opf/disable`` — stop the subprocess and free its memory.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status

from anonymizer.subprocess_opf_client import OPFWorkerError

from ..opf_manager import OPFManager
from ..schemas import OPFStatusSchema

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/opf", tags=["opf"])


def _get_manager(request: Request) -> OPFManager:
    return request.app.state.opf_manager


def _to_response(manager: OPFManager) -> dict:
    s = manager.status()
    return {
        "available": s.available,
        "enabled": s.enabled,
        "loading": s.loading,
        "error": s.error,
        "in_flight_jobs": s.in_flight_jobs,
        "idle_timeout_seconds": s.idle_timeout_seconds,
        "seconds_until_auto_disable": s.seconds_until_auto_disable,
    }


@router.get("/status", response_model=OPFStatusSchema)
def get_status(request: Request) -> dict:
    manager = _get_manager(request)
    return _to_response(manager)


@router.post("/enable", response_model=OPFStatusSchema)
def enable(request: Request) -> dict:
    manager = _get_manager(request)
    if not manager.available:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "OPF não está disponível neste servidor (modo mock). "
                "Reinicie a API sem --mock para usar o modelo."
            ),
        )
    try:
        manager.enable()
    except OPFWorkerError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Falha ao subir o OPF: {exc}",
        )
    return _to_response(manager)


@router.post("/disable", response_model=OPFStatusSchema)
def disable(request: Request) -> dict:
    manager = _get_manager(request)
    manager.disable()
    return _to_response(manager)
