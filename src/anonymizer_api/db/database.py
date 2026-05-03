"""Engine + session factory wrapper. Swap to Postgres by changing ``db_url``."""
from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, db_url: str) -> None:
        self.url = db_url
        connect_args = (
            {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        )
        self.engine = create_engine(db_url, connect_args=connect_args, future=True)
        self._SessionLocal = sessionmaker(
            bind=self.engine, autocommit=False, autoflush=False, future=True
        )

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        # Poor-man's migrations for SQLite — adds columns introduced after
        # the original schema. Postgres deployments would use Alembic.
        if self.url.startswith("sqlite"):
            self._ensure_column(
                "jobs",
                "mode",
                "VARCHAR(40) NOT NULL DEFAULT 'anonymization'",
            )
            self._ensure_column("jobs", "restored_path", "VARCHAR")
            # Sprint 5 — link a job to a container. Existing rows stay
            # null (standalone jobs) and the listing endpoint filters
            # by IS NULL by default.
            self._ensure_column("jobs", "container_id", "VARCHAR(36)")
            self._ensure_column("jobs", "opf_used", "BOOLEAN")
            self._ensure_column(
                "container_documents", "job_id", "VARCHAR(36)"
            )

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        """Add a column if it isn't already present (SQLite only)."""
        with self.engine.begin() as conn:
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            existing = {row[1] for row in rows}
            if column not in existing:
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                )

    def session(self) -> Session:
        return self._SessionLocal()
