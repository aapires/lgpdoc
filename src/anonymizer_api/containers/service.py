"""Business logic for pseudonymization containers.

The service stays deliberately separate from ``JobService`` so the
existing three modes (anonymization / reversible / detector comparison)
remain untouched as this feature grows.

Logging discipline (mirrors the rest of the codebase):
* metadata only — ``container_id``, ``document_id``, name length,
  status, counts
* NEVER log document text, original PII, replacements, the raw
  ``description`` field, or any marker → original mapping. The
  description in particular is user-supplied and may contain PII.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from anonymizer.client import PrivacyFilterClient
from anonymizer.document_models import BLOCK_SEPARATOR
from anonymizer.pipeline import ALLOWED_EXTENSIONS, extract_document
from sqlalchemy.orm import Session

from ..db.models import (
    CONTAINER_DOCUMENT_SOURCE_PSEUDONYMIZED,
    CONTAINER_DOCUMENT_SOURCE_RAW,
    CONTAINER_DOCUMENT_STATUS_FAILED,
    CONTAINER_DOCUMENT_STATUS_PENDING_REVIEW,
    CONTAINER_DOCUMENT_STATUS_PROCESSING,
    CONTAINER_DOCUMENT_STATUS_READY,
    CONTAINER_DOCUMENT_STATUS_REJECTED,
    CONTAINER_STATUS_ACTIVE,
    CONTAINER_STATUS_ARCHIVED,
    ContainerDocumentModel,
    ContainerMappingEntryModel,
    ContainerModel,
)
from ..db.repositories import (
    ContainerDocumentRepository,
    ContainerMappingEntryRepository,
    ContainerRepository,
    ContainerSpanRepository,
)
from ..storage import Storage
from .export_service import export_sensitive_xlsx
from .promote import load_applied_spans, promote_job_spans_to_container
from .restore_service import RestoreSummary, restore_text
from .validation_service import (
    ResidualPiiSpan,
    ValidationSummary,
    detect_residual_pii,
    validate_pseudonymized_text,
)

logger = logging.getLogger(__name__)


_VALID_STATUSES: frozenset[str] = frozenset(
    {CONTAINER_STATUS_ACTIVE, CONTAINER_STATUS_ARCHIVED}
)


class ContainerNotFoundError(LookupError):
    """Raised when a container ID does not exist."""


class ContainerDocumentNotFoundError(LookupError):
    """Raised when a (container_id, document_id) pair does not exist."""


class ContainerValidationError(ValueError):
    """Raised on input validation problems (e.g. bad status)."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ContainerService:
    def __init__(
        self,
        db: Session,
        *,
        storage: Storage | None = None,
        client: PrivacyFilterClient | None = None,
    ) -> None:
        self.db = db
        self.repo = ContainerRepository(db)
        self.docs = ContainerDocumentRepository(db)
        self.mapping = ContainerMappingEntryRepository(db)
        self.spans = ContainerSpanRepository(db)
        # ``storage`` and ``client`` are required for document upload /
        # pseudonymisation. CRUD-only operations (Sprint 1 endpoints)
        # don't need them, so the constructor accepts ``None`` to keep
        # tests / read endpoints free of those dependencies.
        self.storage = storage
        self.client = client

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self, *, name: str, description: str | None = None
    ) -> ContainerModel:
        clean_name = name.strip()
        if not clean_name:
            raise ContainerValidationError("Container name cannot be empty")
        clean_desc = description.strip() if description else None
        if clean_desc == "":
            clean_desc = None

        container_id = str(uuid.uuid4())
        obj = self.repo.create(
            container_id=container_id,
            name=clean_name,
            description=clean_desc,
            status=CONTAINER_STATUS_ACTIVE,
        )
        logger.info(
            "Container created container_id=%s name_len=%d has_description=%s",
            container_id,
            len(clean_name),
            clean_desc is not None,
        )
        return obj

    def get(self, container_id: str) -> ContainerModel:
        obj = self.repo.get(container_id)
        if obj is None:
            raise ContainerNotFoundError(
                f"Container {container_id!r} not found"
            )
        return obj

    def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> list[ContainerModel]:
        if status is not None and status not in _VALID_STATUSES:
            raise ContainerValidationError(
                f"Invalid status filter {status!r}; "
                f"allowed: {sorted(_VALID_STATUSES)}"
            )
        return self.repo.list(limit=limit, offset=offset, status=status)

    def update(
        self,
        container_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> ContainerModel:
        # Existence check up-front so PATCH on a missing container fails
        # before we touch validation.
        self.get(container_id)

        fields: dict[str, str | None] = {}
        if name is not None:
            clean_name = name.strip()
            if not clean_name:
                raise ContainerValidationError("Container name cannot be empty")
            fields["name"] = clean_name
        if description is not None:
            clean_desc = description.strip()
            fields["description"] = clean_desc or None
        if status is not None:
            if status not in _VALID_STATUSES:
                raise ContainerValidationError(
                    f"Invalid status {status!r}; "
                    f"allowed: {sorted(_VALID_STATUSES)}"
                )
            fields["status"] = status

        if not fields:
            # PATCH with empty body is a no-op — return the current row.
            return self.get(container_id)

        updated = self.repo.update(container_id, **fields)
        # ``update`` returns None only if the row vanished between the
        # existence check and the write — extremely unlikely under SQLite.
        if updated is None:  # pragma: no cover — defensive
            raise ContainerNotFoundError(
                f"Container {container_id!r} disappeared during update"
            )
        logger.info(
            "Container updated container_id=%s changed=%s",
            container_id,
            sorted(fields.keys()),
        )
        return updated

    def delete(self, container_id: str) -> None:
        self.get(container_id)  # raise 404 if missing
        ok = self.repo.delete(container_id)
        if not ok:  # pragma: no cover — defensive
            raise ContainerNotFoundError(
                f"Container {container_id!r} disappeared during delete"
            )
        logger.info("Container deleted container_id=%s", container_id)

    # ------------------------------------------------------------------
    # Aggregates
    # ------------------------------------------------------------------

    def count_documents(self, container_id: str) -> int:
        return self.docs.count_for_container(container_id)

    def count_markers(self, container_id: str) -> int:
        return self.mapping.count_for_container(container_id)

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    def list_documents(
        self, container_id: str
    ) -> list[ContainerDocumentModel]:
        self.get(container_id)  # 404 if container missing
        return self.docs.list_for_container(container_id)

    def get_document(
        self, container_id: str, document_id: str
    ) -> ContainerDocumentModel:
        self.get(container_id)
        doc = self.docs.get(container_id, document_id)
        if doc is None:
            raise ContainerDocumentNotFoundError(
                f"Document {document_id!r} not found in container "
                f"{container_id!r}"
            )
        return doc

    def add_raw_document(
        self,
        container_id: str,
        *,
        filename: str,
        content: bytes,
        role: str = "source",
        job_factory: Callable[[Session], Any] | None = None,
    ) -> ContainerDocumentModel:
        """Submit a raw sensitive document to the container's review
        queue.

        Sprint 5 reroutes this through the regular jobs subsystem so
        the document goes through the full review UX (false-positive
        marking, manual redaction, approve/reject) before its spans are
        promoted into the container's marker table.

        The flow:
            1. Validate (container exists, file extension allowed)
            2. Hand the file to ``JobService.submit_upload`` — it
               persists to the standard quarantine and creates a
               ``JobModel`` row tagged with this ``container_id``
            3. Create a ``ContainerDocumentModel`` linked to the job
               with status ``processing``
            4. The caller (the router) is responsible for kicking the
               background pipeline; once it finishes, the approve/reject
               hooks promote the document to ``ready`` / ``rejected``

        ``job_factory`` is a callable that builds a ``JobService`` for
        the current session — kept as a parameter so the dependency on
        ``JobService`` lives at the router boundary, not inside the
        container service module (preserves the architectural
        invariant ``containers/`` does not import ``jobs``).
        """
        if self.storage is None or self.client is None:
            raise ContainerValidationError(
                "Document upload requires a configured storage and "
                "client — instantiate the service via the app factory."
            )
        if job_factory is None:
            raise ContainerValidationError(
                "Document upload requires a job_factory; the router "
                "wires it via dependency injection."
            )
        self.get(container_id)

        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ContainerValidationError(
                f"Extension {ext!r} not allowed. "
                f"Allowed: {sorted(ALLOWED_EXTENSIONS)}"
            )
        if not content:
            raise ContainerValidationError("Empty file")

        # Hand the file to the regular jobs subsystem so the review UI
        # gets it for free.
        job_service = job_factory(self.db)
        job = job_service.submit_upload(
            filename, content, mode="anonymization"
        )
        # Tag the job as part of this container so list endpoints can
        # filter it out of the standalone view and the approve/reject
        # hooks can find the matching ContainerDocument row.
        job_service.jobs.update(job.job_id, container_id=container_id)

        document_id = str(uuid.uuid4())
        doc = self.docs.create(
            document_id=document_id,
            container_id=container_id,
            job_id=job.job_id,
            filename=filename,
            source_type=CONTAINER_DOCUMENT_SOURCE_RAW,
            role=role,
            status=CONTAINER_DOCUMENT_STATUS_PROCESSING,
            file_format=ext.lstrip("."),
            file_hash=job.file_hash,
            file_size=job.file_size,
            quarantine_path=job.quarantine_path,
        )
        logger.info(
            "Container document submitted to review container_id=%s "
            "document_id=%s job_id=%s format=%s size=%d role=%s",
            container_id,
            document_id,
            job.job_id,
            ext,
            len(content),
            role,
        )
        return doc

    def mark_pending_review(
        self, container_id: str, document_id: str
    ) -> ContainerDocumentModel | None:
        """Called by the post-processing hook in JobService once the
        pipeline finishes successfully — flips the document into
        ``pending_review`` so the UI knows the user can act."""
        return self.docs.update(
            container_id,
            document_id,
            status=CONTAINER_DOCUMENT_STATUS_PENDING_REVIEW,
        )

    def mark_failed(
        self,
        container_id: str,
        document_id: str,
        error_message: str | None = None,
    ) -> ContainerDocumentModel | None:
        return self.docs.update(
            container_id,
            document_id,
            status=CONTAINER_DOCUMENT_STATUS_FAILED,
            error_message=error_message,
        )

    def mark_rejected(
        self, container_id: str, document_id: str
    ) -> ContainerDocumentModel | None:
        return self.docs.update(
            container_id,
            document_id,
            status=CONTAINER_DOCUMENT_STATUS_REJECTED,
        )

    def promote_approved_job(
        self,
        *,
        container_id: str,
        document_id: str,
        redacted_path: str,
        spans_path: str,
    ) -> ContainerDocumentModel:
        """Run the post-approval promotion: translate the job's
        applied spans into container markers, persist ContainerSpans,
        write the pseudonymised artefact, and flip the document into
        ``ready``."""
        if self.storage is None:
            raise ContainerValidationError(
                "Storage required for promotion"
            )
        # Existence check (raises if missing).
        self.get_document(container_id, document_id)

        redacted_text = Path(redacted_path).read_text(encoding="utf-8")
        applied_spans = load_applied_spans(spans_path)
        result = promote_job_spans_to_container(
            container_id=container_id,
            document_id=document_id,
            redacted_text=redacted_text,
            applied_spans=applied_spans,
            mapping_repo=self.mapping,
            span_repo=self.spans,
        )

        container_dir = self._container_storage_dir(container_id)
        container_dir.mkdir(parents=True, exist_ok=True)
        pseudonymized_path = container_dir / f"{document_id}.pseudonymized.txt"
        pseudonymized_path.write_text(
            result.pseudonymized_text, encoding="utf-8"
        )
        updated = self.docs.update(
            container_id,
            document_id,
            status=CONTAINER_DOCUMENT_STATUS_READY,
            pseudonymized_path=str(pseudonymized_path),
        )
        assert updated is not None
        logger.info(
            "Container document promoted container_id=%s document_id=%s "
            "spans=%d new_markers=%d reused_markers=%d",
            container_id,
            document_id,
            result.span_count,
            result.new_markers,
            result.reused_markers,
        )
        return updated

    def add_pseudonymized_document(
        self,
        container_id: str,
        *,
        filename: str,
        content: bytes,
        role: str = "edited_version",
    ) -> ContainerDocumentModel:
        """Import an already-pseudonymized document into the container.

        This flow is **deliberately distinct** from raw document ingest:

        * No detection runs.
        * No mapping entries are created — markers found in the text
          are validated against the existing mapping table only.
        * The artefact is stored as-is (no replacement / pseudonymisation)
          and ``pseudonymized_path`` points at the same bytes that
          arrived in the upload.

        The validation summary is persisted on the document row so the
        UI can show known / unknown / malformed marker counts.
        """
        if self.storage is None:
            raise ContainerValidationError(
                "Document upload requires a configured storage — "
                "instantiate the service via the app factory."
            )
        self.get(container_id)

        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ContainerValidationError(
                f"Extension {ext!r} not allowed. "
                f"Allowed: {sorted(ALLOWED_EXTENSIONS)}"
            )
        if not content:
            raise ContainerValidationError("Empty file")

        document_id = str(uuid.uuid4())
        container_dir = self._container_storage_dir(container_id)
        container_dir.mkdir(parents=True, exist_ok=True)
        # Original upload (may be binary: .docx, .pdf). Kept under
        # ``quarantine_path`` for audit but never served as the
        # downloadable artefact.
        quarantine_path = container_dir / f"{document_id}{ext}"
        quarantine_path.write_bytes(content)

        file_hash = _sha256_bytes(content)
        doc = self.docs.create(
            document_id=document_id,
            container_id=container_id,
            filename=filename,
            source_type=CONTAINER_DOCUMENT_SOURCE_PSEUDONYMIZED,
            role=role,
            status=CONTAINER_DOCUMENT_STATUS_PROCESSING,
            file_format=ext.lstrip("."),
            file_hash=file_hash,
            file_size=len(content),
            quarantine_path=str(quarantine_path),
            # ``pseudonymized_path`` is set after extraction so it
            # always points at a UTF-8 text file (download endpoint
            # assumes text). Leaving it null until then.
        )

        logger.info(
            "Pseudonymized document upload accepted container_id=%s "
            "document_id=%s format=%s size=%d hash=%s role=%s",
            container_id,
            document_id,
            ext,
            len(content),
            file_hash,
            role,
        )

        try:
            extraction = extract_document(quarantine_path)
            text = BLOCK_SEPARATOR.join(b.text for b in extraction.blocks)
            summary = validate_pseudonymized_text(
                container_id=container_id,
                text=text,
                repo=self.mapping,
            )
        except Exception as exc:  # noqa: BLE001
            self.docs.update(
                container_id,
                document_id,
                status=CONTAINER_DOCUMENT_STATUS_FAILED,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            logger.exception(
                "Pseudonymized document validation failed container_id=%s "
                "document_id=%s",
                container_id,
                document_id,
            )
            raise

        # Save the extracted text as the canonical pseudonymised
        # artefact. For .txt / .md uploads this is a near-identity
        # copy; for .docx / .pdf it's the flattened plain text.
        # The download endpoint serves THIS file — never the binary.
        pseudonymized_path = container_dir / f"{document_id}.pseudonymized.txt"
        pseudonymized_path.write_text(text, encoding="utf-8")

        # Persist the summary on the document row. The doc lands in
        # ``pending_review`` — the operator must approve via the
        # dedicated pseudonymized review screen before it becomes
        # ``ready``. JSON payload only carries markers (non-sensitive)
        # and counts; original values from the mapping never leak here.
        import json as _json
        updated = self.docs.update(
            container_id,
            document_id,
            status=CONTAINER_DOCUMENT_STATUS_PENDING_REVIEW,
            pseudonymized_path=str(pseudonymized_path),
            validation_summary_json=_json.dumps(summary.to_dict()),
        )
        assert updated is not None
        return updated

    # ------------------------------------------------------------------
    # Pseudonymized-review flow (Sprint 5+)
    # ------------------------------------------------------------------

    def build_pseudonymized_review_payload(
        self, container_id: str, document_id: str
    ) -> dict[str, object]:
        """Compose everything the dedicated review screen needs:
        text, validation summary, residual PII spans."""
        if self.client is None:
            raise ContainerValidationError(
                "Review payload requires a configured client — "
                "instantiate the service via the app factory."
            )
        doc = self.get_document(container_id, document_id)
        if doc.source_type != CONTAINER_DOCUMENT_SOURCE_PSEUDONYMIZED:
            raise ContainerValidationError(
                "This review flow is only for already-pseudonymized "
                "documents. Raw uploads use /jobs/{job_id}/review."
            )
        if not doc.pseudonymized_path:
            raise ContainerValidationError(
                f"Document {document_id!r} has no pseudonymized "
                f"artefact yet (status={doc.status!r})."
            )
        text = Path(doc.pseudonymized_path).read_text(encoding="utf-8")
        summary = validate_pseudonymized_text(
            container_id=container_id, text=text, repo=self.mapping
        )
        residual = detect_residual_pii(text=text, client=self.client)
        return {
            "document_id": document_id,
            "container_id": container_id,
            "status": doc.status,
            "filename": doc.filename,
            "text": text,
            "validation": summary.to_dict(),
            "residual_pii": [
                {
                    "start": r.start,
                    "end": r.end,
                    "entity_type": r.entity_type,
                    "confidence": r.confidence,
                    "detection_source": r.detection_source,
                    "fragment": r.fragment,
                    "fragment_hash": r.fragment_hash,
                }
                for r in residual
            ],
        }

    def approve_pseudonymized_document(
        self, container_id: str, document_id: str
    ) -> ContainerDocumentModel:
        doc = self.get_document(container_id, document_id)
        if doc.source_type != CONTAINER_DOCUMENT_SOURCE_PSEUDONYMIZED:
            raise ContainerValidationError(
                "Approve action is for already-pseudonymized documents. "
                "Raw uploads use /jobs/{job_id}/approve."
            )
        if doc.status != CONTAINER_DOCUMENT_STATUS_PENDING_REVIEW:
            raise ContainerValidationError(
                f"Cannot approve in status {doc.status!r}; expected "
                f"{CONTAINER_DOCUMENT_STATUS_PENDING_REVIEW!r}."
            )
        updated = self.docs.update(
            container_id,
            document_id,
            status=CONTAINER_DOCUMENT_STATUS_READY,
        )
        assert updated is not None
        logger.info(
            "Pseudonymized document approved container_id=%s document_id=%s",
            container_id,
            document_id,
        )
        return updated

    def reject_pseudonymized_document(
        self, container_id: str, document_id: str
    ) -> ContainerDocumentModel:
        doc = self.get_document(container_id, document_id)
        if doc.source_type != CONTAINER_DOCUMENT_SOURCE_PSEUDONYMIZED:
            raise ContainerValidationError(
                "Reject action is for already-pseudonymized documents."
            )
        if doc.status != CONTAINER_DOCUMENT_STATUS_PENDING_REVIEW:
            raise ContainerValidationError(
                f"Cannot reject in status {doc.status!r}; expected "
                f"{CONTAINER_DOCUMENT_STATUS_PENDING_REVIEW!r}."
            )
        updated = self.docs.update(
            container_id,
            document_id,
            status=CONTAINER_DOCUMENT_STATUS_REJECTED,
        )
        assert updated is not None
        logger.info(
            "Pseudonymized document rejected container_id=%s document_id=%s",
            container_id,
            document_id,
        )
        return updated

    def apply_manual_redaction_to_pseudonymized(
        self,
        container_id: str,
        document_id: str,
        *,
        fragment: str,
        entity_type: str,
    ) -> dict[str, object]:
        """Anonymise residual PII the reviewer found in a
        pseudonymized document.

        This is **distinct** from interpreting unknown markers (which
        the spec explicitly forbids creating entries for). Here the
        operator is replacing real PII text — content that escaped
        prior anonymisation — with a fresh container marker. That's
        a legitimate creation event for the mapping table.

        Steps:
            1. Resolve / allocate a marker for ``fragment`` via the
               container's MarkerResolver (reuses existing entry if
               this fragment was already mapped).
            2. Replace EVERY occurrence of ``fragment`` in the
               pseudonymized text with the marker.
            3. Re-validate the new text; persist the updated summary.
        """
        from .marker_resolver import MarkerResolver

        if not fragment.strip():
            raise ContainerValidationError(
                "Selected fragment is empty"
            )
        doc = self.get_document(container_id, document_id)
        if doc.source_type != CONTAINER_DOCUMENT_SOURCE_PSEUDONYMIZED:
            raise ContainerValidationError(
                "Manual redaction here is for already-pseudonymized "
                "documents. Raw uploads use /jobs/{job_id} actions."
            )
        if doc.status != CONTAINER_DOCUMENT_STATUS_PENDING_REVIEW:
            raise ContainerValidationError(
                f"Manual redaction not allowed in status {doc.status!r}; "
                f"expected {CONTAINER_DOCUMENT_STATUS_PENDING_REVIEW!r}."
            )
        if not doc.pseudonymized_path:
            raise ContainerValidationError(
                "Document has no pseudonymized artefact"
            )

        resolver = MarkerResolver(self.mapping, container_id)
        resolved = resolver.resolve(
            entity_type=entity_type,
            original_text=fragment,
            detection_source="manual_pseudonymized_review",
            document_id=document_id,
        )

        path = Path(doc.pseudonymized_path)
        old_text = path.read_text(encoding="utf-8")
        if fragment not in old_text:
            raise ContainerValidationError(
                "Selected fragment not found in the current text. "
                "Reload the page and select again."
            )
        occurrences = old_text.count(fragment)
        new_text = old_text.replace(fragment, resolved.marker)
        path.write_text(new_text, encoding="utf-8")

        # Re-run validation on the new text + persist updated summary.
        summary = validate_pseudonymized_text(
            container_id=container_id,
            text=new_text,
            repo=self.mapping,
        )
        import json as _json
        self.docs.update(
            container_id,
            document_id,
            validation_summary_json=_json.dumps(summary.to_dict()),
        )
        logger.info(
            "Manual redaction applied to pseudonymized document "
            "container_id=%s document_id=%s entity_type=%s "
            "occurrences=%d marker_created=%s",
            container_id,
            document_id,
            entity_type,
            occurrences,
            resolved.created,
        )
        return {
            "marker": resolved.marker,
            "occurrences": occurrences,
            "marker_created": resolved.created,
            "validation": summary.to_dict(),
        }

    def validate_pseudonymized_text(
        self, container_id: str, text: str
    ) -> ValidationSummary:
        """Validate a free-form pseudonymized text against the
        container's mapping. No persistence — useful for a UI "paste
        and check" flow before deciding to upload."""
        self.get(container_id)
        return validate_pseudonymized_text(
            container_id=container_id,
            text=text,
            repo=self.mapping,
        )

    def delete_document(self, container_id: str, document_id: str) -> None:
        doc = self.get_document(container_id, document_id)
        self.spans.delete_for_document(document_id)
        self.docs.delete(container_id, document_id)
        for path_str in (doc.quarantine_path, doc.pseudonymized_path):
            if not path_str:
                continue
            path = Path(path_str)
            if path.exists():
                try:
                    path.unlink()
                except OSError:  # pragma: no cover — best-effort cleanup
                    logger.warning(
                        "Failed to remove document file container_id=%s "
                        "document_id=%s",
                        container_id,
                        document_id,
                    )
        logger.info(
            "Container document deleted container_id=%s document_id=%s",
            container_id,
            document_id,
        )

    def read_pseudonymized_text(
        self, container_id: str, document_id: str
    ) -> str:
        doc = self.get_document(container_id, document_id)
        if not doc.pseudonymized_path:
            raise ContainerValidationError(
                f"Document {document_id!r} has no pseudonymized artefact "
                f"on disk yet (status={doc.status!r})."
            )
        try:
            return Path(doc.pseudonymized_path).read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            # Defensive: legacy records (created before the
            # pseudonymized-import fix) may point at the binary upload
            # rather than the extracted text. Surface a clean 4xx
            # instead of a 500 + stack trace.
            raise ContainerValidationError(
                "Pseudonymized artefact on disk is not UTF-8 text — this "
                "document was likely created by an older version of the "
                "service. Re-upload the file to regenerate the artefact."
            ) from exc

    def download_filename_for(self, doc: ContainerDocumentModel) -> str:
        """Compose a human-friendly filename for the downloaded
        pseudonymised artefact: keep the original stem and slot
        ``.pseudonymized.txt`` in. The pseudonymised payload is always
        plain text, regardless of the original format (the pipeline
        flattens during extraction)."""
        stem = Path(doc.filename).stem
        return f"{stem}.pseudonymized.txt"

    def build_pseudonymized_bundle(
        self, container_id: str
    ) -> tuple[bytes, int]:
        """Return ``(zip_bytes, file_count)`` — a ZIP archive bundling
        the pseudonymised artefact of every ``ready`` document in the
        container.

        Documents that aren't yet ``ready`` (or for some reason missing
        their artefact on disk) are skipped — the caller decides what
        to do when ``file_count == 0``.
        """
        import io
        import zipfile

        self.get(container_id)
        rows = self.docs.list_for_container(container_id)

        buf = io.BytesIO()
        used_names: set[str] = set()
        included = 0
        with zipfile.ZipFile(
            buf, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            for doc in rows:
                if not doc.pseudonymized_path:
                    continue
                path = Path(doc.pseudonymized_path)
                if not path.exists():
                    continue
                # Defensive: legacy records (older code) might have
                # ``pseudonymized_path`` pointing at the binary upload.
                # Skip those rather than ship a binary with a .txt
                # extension inside the zip.
                if path.suffix.lower() != ".txt":
                    logger.warning(
                        "Skipping legacy non-text artefact in bundle "
                        "container_id=%s document_id=%s",
                        container_id,
                        doc.document_id,
                    )
                    continue
                arcname = self.download_filename_for(doc)
                # Disambiguate identical filenames across documents by
                # appending the document_id prefix when needed.
                if arcname in used_names:
                    short = doc.document_id[:8]
                    stem = Path(doc.filename).stem
                    arcname = f"{stem}-{short}.pseudonymized.txt"
                used_names.add(arcname)
                zf.write(path, arcname=arcname)
                included += 1

        logger.info(
            "Bundle built container_id=%s files=%d bytes=%d",
            container_id,
            included,
            buf.tell(),
        )
        return buf.getvalue(), included

    # ------------------------------------------------------------------
    # Mapping (conversion table)
    # ------------------------------------------------------------------

    def list_mapping(
        self, container_id: str
    ) -> list[ContainerMappingEntryModel]:
        self.get(container_id)
        return self.mapping.list_for_container(container_id)

    def export_mapping_sensitive(self, container_id: str) -> bytes:
        """XLSX bytes that INCLUDE ``original_text``. The router gates
        this on the explicit sensitive endpoint; this method does not
        check permissions on its own."""
        self.get(container_id)
        return export_sensitive_xlsx(
            container_id=container_id, repo=self.mapping
        )

    # ------------------------------------------------------------------
    # Restoration (Sprint 4)
    # ------------------------------------------------------------------

    def restore_text(
        self, container_id: str, text: str
    ) -> RestoreSummary:
        """Replace every marker in ``text`` registered in this
        container's mapping with its original value. Unknown / malformed
        markers are reported and left untouched."""
        self.get(container_id)
        return restore_text(
            container_id=container_id,
            text=text,
            repo=self.mapping,
        )

    def restore_document(
        self, container_id: str, document_id: str
    ) -> RestoreSummary:
        """Restore the document's pseudonymized artefact using the
        container's mapping. The document must be ``ready`` and have a
        ``pseudonymized_path`` on disk — the service ignores raw
        documents that haven't been processed yet."""
        doc = self.get_document(container_id, document_id)
        if not doc.pseudonymized_path:
            raise ContainerValidationError(
                f"Document {document_id!r} has no pseudonymized "
                f"artefact yet (status={doc.status!r})."
            )
        try:
            text = Path(doc.pseudonymized_path).read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            # See ``read_pseudonymized_text`` — same legacy guard.
            raise ContainerValidationError(
                "Pseudonymized artefact on disk is not UTF-8 text — this "
                "document was likely created by an older version of the "
                "service. Re-upload the file to regenerate the artefact."
            ) from exc
        summary = restore_text(
            container_id=container_id,
            text=text,
            repo=self.mapping,
        )
        logger.info(
            "Document restore done container_id=%s document_id=%s "
            "tokens=%d unique=%d unknown=%d malformed=%d",
            container_id,
            document_id,
            summary.replaced_token_count,
            summary.replaced_unique_count,
            len(summary.unknown_markers),
            len(summary.malformed_markers),
        )
        return summary

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _container_storage_dir(self, container_id: str) -> Path:
        """Per-container subdirectory under ``output_dir`` for documents
        and pseudonymised artefacts."""
        assert self.storage is not None
        return self.storage.output_dir / "containers" / container_id
