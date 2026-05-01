"""Filesystem layout for quarantined uploads and processed artefacts."""
from __future__ import annotations

from pathlib import Path


class Storage:
    """Owns two roots: ``quarantine_dir`` for raw uploads, ``output_dir`` for results.

    Each job gets its own subdirectory under ``output_dir``. The original
    upload is kept under ``quarantine_dir`` for audit (retention policy is
    out of scope for the MVP).
    """

    def __init__(self, quarantine_dir: Path, output_dir: Path) -> None:
        self.quarantine_dir = Path(quarantine_dir)
        self.output_dir = Path(output_dir)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def quarantine_for(self, job_id: str, ext: str) -> Path:
        return self.quarantine_dir / f"{job_id}{ext}"

    def output_for(self, job_id: str) -> Path:
        path = self.output_dir / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path
