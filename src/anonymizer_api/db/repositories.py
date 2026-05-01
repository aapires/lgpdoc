"""Repository layer over SQLAlchemy models.

Routers and services depend only on these classes, so swapping SQLite for
Postgres (or any other store) means re-implementing this module — nothing
else.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import (
    ContainerDocumentModel,
    ContainerMappingEntryModel,
    ContainerModel,
    ContainerSpanModel,
    DetectedSpanModel,
    JobModel,
    PolicyVersionModel,
    ReviewEventModel,
)


class JobRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **fields: Any) -> JobModel:
        job = JobModel(**fields)
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def get(self, job_id: str) -> JobModel | None:
        return self.db.get(JobModel, job_id)

    def update(self, job_id: str, **fields: Any) -> JobModel | None:
        job = self.get(job_id)
        if job is None:
            return None
        for key, value in fields.items():
            setattr(job, key, value)
        self.db.commit()
        self.db.refresh(job)
        return job

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        *,
        only_standalone: bool = True,
    ) -> list[JobModel]:
        """List jobs.

        ``only_standalone`` (default True) hides jobs linked to a
        container — those have their own UI in ``/containers/{id}`` and
        would otherwise pollute the regular documents view.
        """
        stmt = select(JobModel).order_by(JobModel.created_at.desc())
        if status is not None:
            stmt = stmt.where(JobModel.status == status)
        if only_standalone:
            stmt = stmt.where(JobModel.container_id.is_(None))
        stmt = stmt.limit(limit).offset(offset)
        return list(self.db.execute(stmt).scalars())

    def delete(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        self.db.delete(job)
        self.db.commit()
        return True


class SpanRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add_many(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        self.db.add_all([DetectedSpanModel(**row) for row in rows])
        self.db.commit()

    def list_for_job(self, job_id: str) -> list[DetectedSpanModel]:
        stmt = select(DetectedSpanModel).where(DetectedSpanModel.job_id == job_id)
        return list(self.db.execute(stmt).scalars())

    def delete_for_job(self, job_id: str) -> int:
        result = self.db.execute(
            delete(DetectedSpanModel).where(DetectedSpanModel.job_id == job_id)
        )
        self.db.commit()
        return result.rowcount or 0


class ReviewRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, **fields: Any) -> ReviewEventModel:
        event = ReviewEventModel(**fields)
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def list_for_job(self, job_id: str) -> list[ReviewEventModel]:
        stmt = (
            select(ReviewEventModel)
            .where(ReviewEventModel.job_id == job_id)
            .order_by(ReviewEventModel.created_at)
        )
        return list(self.db.execute(stmt).scalars())

    def delete_for_job(self, job_id: str) -> int:
        result = self.db.execute(
            delete(ReviewEventModel).where(ReviewEventModel.job_id == job_id)
        )
        self.db.commit()
        return result.rowcount or 0


class PolicyVersionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_or_create(self, policy_path: str, policy_hash: str) -> PolicyVersionModel:
        stmt = select(PolicyVersionModel).where(
            PolicyVersionModel.policy_hash == policy_hash
        )
        existing = self.db.execute(stmt).scalar_one_or_none()
        if existing is not None:
            return existing
        pv = PolicyVersionModel(policy_path=policy_path, policy_hash=policy_hash)
        self.db.add(pv)
        self.db.commit()
        self.db.refresh(pv)
        return pv


class ContainerRepository:
    """Persistence for pseudonymization workspaces.

    Every method that reads container-scoped data must take
    ``container_id`` as a filter — keeping all such access in one place
    makes the isolation easy to audit.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **fields: Any) -> ContainerModel:
        obj = ContainerModel(**fields)
        self.db.add(obj)
        self.db.commit()
        self.db.refresh(obj)
        return obj

    def get(self, container_id: str) -> ContainerModel | None:
        return self.db.get(ContainerModel, container_id)

    def update(self, container_id: str, **fields: Any) -> ContainerModel | None:
        obj = self.get(container_id)
        if obj is None:
            return None
        for key, value in fields.items():
            setattr(obj, key, value)
        self.db.commit()
        self.db.refresh(obj)
        return obj

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> list[ContainerModel]:
        stmt = select(ContainerModel).order_by(ContainerModel.created_at.desc())
        if status is not None:
            stmt = stmt.where(ContainerModel.status == status)
        stmt = stmt.limit(limit).offset(offset)
        return list(self.db.execute(stmt).scalars())

    def delete(self, container_id: str) -> bool:
        obj = self.get(container_id)
        if obj is None:
            return False
        self.db.delete(obj)
        self.db.commit()
        return True


class ContainerDocumentRepository:
    """Per-container document index. Every read takes ``container_id``
    as a filter — the ``document_id`` alone is never trusted to scope
    a query."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **fields: Any) -> ContainerDocumentModel:
        obj = ContainerDocumentModel(**fields)
        self.db.add(obj)
        self.db.commit()
        self.db.refresh(obj)
        return obj

    def get(
        self, container_id: str, document_id: str
    ) -> ContainerDocumentModel | None:
        stmt = select(ContainerDocumentModel).where(
            ContainerDocumentModel.container_id == container_id,
            ContainerDocumentModel.document_id == document_id,
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def update(
        self, container_id: str, document_id: str, **fields: Any
    ) -> ContainerDocumentModel | None:
        obj = self.get(container_id, document_id)
        if obj is None:
            return None
        for key, value in fields.items():
            setattr(obj, key, value)
        self.db.commit()
        self.db.refresh(obj)
        return obj

    def list_for_container(
        self, container_id: str
    ) -> list[ContainerDocumentModel]:
        stmt = (
            select(ContainerDocumentModel)
            .where(ContainerDocumentModel.container_id == container_id)
            .order_by(ContainerDocumentModel.created_at.desc())
        )
        return list(self.db.execute(stmt).scalars())

    def find_by_job_id(
        self, job_id: str
    ) -> ContainerDocumentModel | None:
        """Look up the container document driven by a given job. Used
        by the approval / rejection hooks to keep the two rows in sync.
        Returns None for standalone jobs (which don't belong to a
        container)."""
        stmt = select(ContainerDocumentModel).where(
            ContainerDocumentModel.job_id == job_id
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def count_for_container(self, container_id: str) -> int:
        stmt = select(ContainerDocumentModel).where(
            ContainerDocumentModel.container_id == container_id
        )
        return len(self.db.execute(stmt).scalars().all())

    def delete(self, container_id: str, document_id: str) -> bool:
        obj = self.get(container_id, document_id)
        if obj is None:
            return False
        self.db.delete(obj)
        self.db.commit()
        return True


class ContainerMappingEntryRepository:
    """Mapping entries (marker ↔ original_value) scoped per container.

    The ``container_id`` filter is non-negotiable. ``find_by_normalized``
    is the central lookup the marker resolver uses; new code must not
    bypass it with ad-hoc queries.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **fields: Any) -> ContainerMappingEntryModel:
        obj = ContainerMappingEntryModel(**fields)
        self.db.add(obj)
        self.db.commit()
        self.db.refresh(obj)
        return obj

    def find_by_normalized(
        self,
        container_id: str,
        entity_type: str,
        normalized_value: str,
    ) -> ContainerMappingEntryModel | None:
        stmt = select(ContainerMappingEntryModel).where(
            ContainerMappingEntryModel.container_id == container_id,
            ContainerMappingEntryModel.entity_type == entity_type,
            ContainerMappingEntryModel.normalized_value == normalized_value,
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def find_by_marker(
        self, container_id: str, marker: str
    ) -> ContainerMappingEntryModel | None:
        stmt = select(ContainerMappingEntryModel).where(
            ContainerMappingEntryModel.container_id == container_id,
            ContainerMappingEntryModel.marker == marker,
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def list_for_container(
        self, container_id: str
    ) -> list[ContainerMappingEntryModel]:
        stmt = (
            select(ContainerMappingEntryModel)
            .where(ContainerMappingEntryModel.container_id == container_id)
            .order_by(
                ContainerMappingEntryModel.entity_type,
                ContainerMappingEntryModel.marker,
            )
        )
        return list(self.db.execute(stmt).scalars())

    def count_for_container(self, container_id: str) -> int:
        stmt = select(ContainerMappingEntryModel).where(
            ContainerMappingEntryModel.container_id == container_id
        )
        return len(self.db.execute(stmt).scalars().all())

    def max_index_for_label(
        self, container_id: str, label: str
    ) -> int:
        """Return the highest index NN already used for ``[LABEL_NN]``
        markers in this container. Returns 0 when no markers exist yet
        for that label.

        We scan in Python rather than relying on a database-specific
        substring extraction — the index space is small (per container)
        and this keeps the code portable across SQLite / Postgres.
        """
        stmt = select(ContainerMappingEntryModel.marker).where(
            ContainerMappingEntryModel.container_id == container_id,
            ContainerMappingEntryModel.marker.like(f"[{label}\\_%]", escape="\\"),
        )
        prefix = f"[{label}_"
        max_idx = 0
        for marker in self.db.execute(stmt).scalars():
            if not (marker.startswith(prefix) and marker.endswith("]")):
                continue
            inner = marker[len(prefix) : -1]
            try:
                idx = int(inner)
            except ValueError:
                continue
            if idx > max_idx:
                max_idx = idx
        return max_idx

    def touch_last_seen(
        self, entry: ContainerMappingEntryModel, ts: datetime | None = None
    ) -> None:
        entry.last_seen_at = ts or datetime.now(timezone.utc)
        self.db.commit()

    def list_occurrences_by_entry(
        self, container_id: str, entry_ids: list[int]
    ) -> dict[int, list[tuple[str, str]]]:
        """Return ``{entry_id: [(document_id, filename), ...]}`` —
        every distinct container document where each mapping entry was
        seen.

        The primary source is ``ContainerSpan`` rows (created when a
        raw doc is promoted post-review). A secondary fallback uses
        ``created_from_document_id`` so entries that exist via
        pseudonymized-flow code paths (which don't emit spans) still
        show at least their creating document.
        """
        out: dict[int, list[tuple[str, str]]] = {}
        if not entry_ids:
            return out

        # 1) Span-based occurrences
        span_stmt = (
            select(
                ContainerSpanModel.mapping_entry_id,
                ContainerSpanModel.container_document_id,
                ContainerDocumentModel.filename,
            )
            .join(
                ContainerDocumentModel,
                ContainerDocumentModel.document_id
                == ContainerSpanModel.container_document_id,
            )
            .where(
                ContainerSpanModel.mapping_entry_id.in_(entry_ids),
                ContainerDocumentModel.container_id == container_id,
            )
        )
        for entry_id, doc_id, filename in self.db.execute(span_stmt).all():
            bucket = out.setdefault(entry_id, [])
            if not any(d == doc_id for d, _ in bucket):
                bucket.append((doc_id, filename))

        # 2) Fallback for entries that have NO span rows: use the
        # ``created_from_document_id`` as a single-occurrence approximation.
        entries_without_spans = [eid for eid in entry_ids if eid not in out]
        if entries_without_spans:
            stmt = select(
                ContainerMappingEntryModel.id,
                ContainerMappingEntryModel.created_from_document_id,
            ).where(ContainerMappingEntryModel.id.in_(entries_without_spans))
            entry_to_doc = {
                eid: doc_id
                for eid, doc_id in self.db.execute(stmt).all()
                if doc_id is not None
            }
            if entry_to_doc:
                doc_stmt = select(
                    ContainerDocumentModel.document_id,
                    ContainerDocumentModel.filename,
                ).where(
                    ContainerDocumentModel.document_id.in_(
                        list(set(entry_to_doc.values()))
                    ),
                    ContainerDocumentModel.container_id == container_id,
                )
                doc_filename = {
                    d: fn for d, fn in self.db.execute(doc_stmt).all()
                }
                for entry_id, doc_id in entry_to_doc.items():
                    if doc_id in doc_filename:
                        out.setdefault(entry_id, []).append(
                            (doc_id, doc_filename[doc_id])
                        )

        # Stable ordering inside each bucket — alphabetical by filename.
        for entry_id in out:
            out[entry_id].sort(key=lambda pair: pair[1].lower())
        return out

    def delete_for_container(self, container_id: str) -> int:
        result = self.db.execute(
            delete(ContainerMappingEntryModel).where(
                ContainerMappingEntryModel.container_id == container_id
            )
        )
        self.db.commit()
        return result.rowcount or 0


class ContainerSpanRepository:
    """Per-document span records — references mapping entries via FK."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def add_many(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        self.db.add_all([ContainerSpanModel(**row) for row in rows])
        self.db.commit()

    def list_for_document(
        self, container_document_id: str
    ) -> list[ContainerSpanModel]:
        stmt = (
            select(ContainerSpanModel)
            .where(
                ContainerSpanModel.container_document_id == container_document_id
            )
            .order_by(ContainerSpanModel.start_char)
        )
        return list(self.db.execute(stmt).scalars())

    def delete_for_document(self, container_document_id: str) -> int:
        result = self.db.execute(
            delete(ContainerSpanModel).where(
                ContainerSpanModel.container_document_id == container_document_id
            )
        )
        self.db.commit()
        return result.rowcount or 0
