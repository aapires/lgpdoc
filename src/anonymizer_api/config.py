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
    use_mock_client: bool = False
    # Origins allowed by CORS — defaults cover the Next.js dev server.
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
