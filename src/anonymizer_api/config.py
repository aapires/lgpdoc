"""Settings loaded from environment variables with the prefix ``ANONYMIZER_API_``."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ANONYMIZER_API_",
        env_file=".env",
        extra="ignore",
    )

    quarantine_dir: Path = Path("./var/quarantine")
    output_dir: Path = Path("./var/output")
    db_url: str = "sqlite:///./var/anonymizer_api.db"
    max_bytes: int = 50 * 1024 * 1024  # 50 MiB
    policy_path: Path = Path("./policies/default.yaml")
    runtime_config_path: Path = Path("./var/runtime_config.json")
    # Use the regex MockPrivacyFilterClient instead of OPF — fast, no model download.
    # Implies opf_manager.available=False; the runtime toggle is hidden in the UI.
    use_mock_client: bool = False
    # When True, the OPF subprocess uses MockPrivacyFilterClient instead of the
    # real OPF model. Tests-only — exercises the subprocess plumbing without
    # requiring torch/opf to be installed.
    opf_use_mock_worker: bool = False
    # Auto-disable OPF after this many seconds of no detect/acquire
    # activity (and zero outstanding leases). Default 300 s (5 min) so a
    # user who turns OPF on, runs a doc, and forgets to flip it back
    # doesn't keep ~3 GB of model weights resident overnight. Set to 0
    # to disable the watchdog entirely.
    opf_idle_timeout_seconds: int = 300
    # Origins allowed by CORS — defaults cover the Next.js dev server.
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
