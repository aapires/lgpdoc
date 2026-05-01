"""FastAPI dependencies — translate ``app.state`` into per-request objects."""
from __future__ import annotations

from typing import Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .containers.service import ContainerService
from .jobs.service import JobService


def get_db(request: Request) -> Generator[Session, None, None]:
    db: Session = request.app.state.database.session()
    try:
        yield db
    finally:
        db.close()


def get_service(
    request: Request, db: Session = Depends(get_db)
) -> JobService:
    return request.app.state.service_factory(db)


def get_container_service(
    request: Request, db: Session = Depends(get_db)
) -> ContainerService:
    """Container service is instantiated per request. We pass the shared
    ``storage`` and the augmented ``client`` from ``app.state`` so the
    pseudonymisation pipeline reuses the same OPF model that the rest
    of the app uses — there's only one expensive resource (the model)
    and it's shared via app state, never re-created per request."""
    return ContainerService(
        db,
        storage=request.app.state.storage,
        client=request.app.state.client,
    )
