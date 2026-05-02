"""FastAPI app factory.

The OPF model is expensive to instantiate, so the client is built once at
app construction and shared across requests through ``app.state``.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from anonymizer.augmentations import make_augmented_client
from anonymizer.client import MockPrivacyFilterClient, PrivacyFilterClient
from anonymizer.regex_only_client import RegexOnlyClient

from .config import Settings
from .containers.service import ContainerService
from .db.database import Database
from .db.models import JobModel
from .jobs.service import JobService
from .opf_manager import OPFManager, ToggledBaseClient
from .routers.containers import router as containers_router
from .routers.detector_comparison import router as detector_comparison_router
from .routers.jobs import router as jobs_router
from .routers.opf import router as opf_router
from .routers.settings import router as settings_router
from .settings_store import SettingsStore
from .storage import Storage

logger = logging.getLogger(__name__)


def _make_clients(
    settings: Settings,
    store: SettingsStore,
    opf_manager: OPFManager,
) -> tuple[PrivacyFilterClient, PrivacyFilterClient]:
    """Build the augmented and regex-only clients shared by all requests.

    The "OPF side" of the diagnostic detector-comparison is no longer
    pre-built here — comparison endpoints request it from
    ``opf_manager`` at call time so they can ``ensure_loaded()`` first.

    Returns ``(augmented, regex_only)``:

    * ``augmented`` — production client (case normalisation + BR
      augmentations + regex detectors), built around a
      :class:`ToggledBaseClient` so OPF can be enabled/disabled at
      runtime without rebuilding the augmented wrapper.
    * ``regex_only`` — every deterministic regex detector, no model.
      Used by the "regex puro" side of the diagnostic comparison.
    """
    base: PrivacyFilterClient = ToggledBaseClient(
        mock=MockPrivacyFilterClient(),
        manager=opf_manager,
    )
    augmented = make_augmented_client(
        base, get_enabled_kinds=store.get_enabled_kinds
    )
    regex_only: PrivacyFilterClient = RegexOnlyClient(
        get_enabled_kinds=store.get_enabled_kinds
    )
    return augmented, regex_only


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    storage = Storage(settings.quarantine_dir, settings.output_dir)
    database = Database(settings.db_url)
    database.create_all()
    settings_store = SettingsStore(settings.runtime_config_path)
    opf_manager = OPFManager(
        available=not settings.use_mock_client,
        use_mock_worker=settings.opf_use_mock_worker,
    )
    client, regex_client = _make_clients(settings, settings_store, opf_manager)

    # ------------------------------------------------------------------
    # Container lifecycle hooks for jobs that belong to a container.
    # The hooks live here (not in JobService and not in ContainerService)
    # to keep the two subsystems unaware of each other at the package
    # level — the architectural invariant ``containers/`` does not
    # import ``jobs/`` and vice versa is enforced by tests.
    # ------------------------------------------------------------------

    def _on_processing_done(db, job: JobModel) -> None:
        if not job.container_id:
            return
        cont_svc = ContainerService(db, storage=storage, client=client)
        doc = cont_svc.docs.find_by_job_id(job.job_id)
        if doc is None:
            return
        if job.status == "failed":
            cont_svc.mark_failed(
                job.container_id, doc.document_id, job.error_message
            )
        else:
            cont_svc.mark_pending_review(job.container_id, doc.document_id)

    def _on_approved(db, job: JobModel) -> None:
        if not job.container_id or not job.redacted_path or not job.spans_path:
            return
        cont_svc = ContainerService(db, storage=storage, client=client)
        doc = cont_svc.docs.find_by_job_id(job.job_id)
        if doc is None:
            return
        cont_svc.promote_approved_job(
            container_id=job.container_id,
            document_id=doc.document_id,
            redacted_path=job.redacted_path,
            spans_path=job.spans_path,
        )

    def _on_rejected(db, job: JobModel) -> None:
        if not job.container_id:
            return
        cont_svc = ContainerService(db, storage=storage, client=client)
        doc = cont_svc.docs.find_by_job_id(job.job_id)
        if doc is None:
            return
        cont_svc.mark_rejected(job.container_id, doc.document_id)

    def service_factory(db) -> JobService:
        return JobService(
            db=db,
            settings=settings,
            storage=storage,
            client=client,
            opf_manager=opf_manager,
            settings_store=settings_store,
            on_processing_done=_on_processing_done,
            on_approved=_on_approved,
            on_rejected=_on_rejected,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            # Don't wait for in-flight jobs — uvicorn is already tearing down.
            opf_manager.disable(wait_for_jobs=False)

    app = FastAPI(
        title="LGPDoc API",
        description="Local document anonymization service with PII detection and verification.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.storage = storage
    app.state.database = database
    app.state.client = client
    app.state.opf_manager = opf_manager
    app.state.regex_client = regex_client
    app.state.service_factory = service_factory
    app.state.settings_store = settings_store

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(jobs_router)
    app.include_router(settings_router)
    app.include_router(detector_comparison_router)
    app.include_router(containers_router)
    app.include_router(opf_router)
    return app
