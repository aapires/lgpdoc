"""Runtime settings store — which detectors are enabled.

Persisted to a JSON file so changes survive restarts. Cached in-process
to avoid hitting disk on every detection call.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# All detector kinds the system can produce. Keep in sync with the policy
# YAML and the manual-redaction dropdown in the UI.
ALL_KINDS: tuple[str, ...] = (
    # Identity documents
    "cpf",
    "cnpj",
    "rg",
    "cnh",
    "passaporte",
    "titulo_eleitor",
    "pis",
    "ctps",
    "sus",
    # Professional registries
    "oab",
    "crm",
    "crea",
    # Vehicle data
    "placa",
    "renavam",
    # Legal / fiscal
    "processo_cnj",
    "inscricao_estadual",
    # PII categories produced by OPF + augmentations
    "private_person",
    "private_company",  # legal entities (companies + government bodies)
    "private_email",
    "private_phone",
    "private_address",
    "private_date",
    "private_url",
    "account_number",
    "secret",
    # Brazilian-specific patterns
    "cep",
    "ip",
    "financeiro",
)

# Defaults: enable every kind unless we know it's unreliable enough to be
# off by default. Currently we enable everything — the user disables
# whatever produces too many false positives in their corpus.
DEFAULT_ENABLED: frozenset[str] = frozenset(ALL_KINDS)


@dataclass
class RuntimeSettings:
    enabled_detectors: set[str] = field(default_factory=lambda: set(DEFAULT_ENABLED))

    def to_dict(self) -> dict[str, Any]:
        return {"enabled_detectors": sorted(self.enabled_detectors)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeSettings":
        kinds = data.get("enabled_detectors")
        if kinds is None:
            return cls()
        return cls(enabled_detectors=set(kinds))


class SettingsStore:
    """Thread-safe JSON-backed cache of the runtime settings."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._cache: RuntimeSettings | None = None

    def get(self) -> RuntimeSettings:
        with self._lock:
            if self._cache is None:
                self._cache = self._load()
            # Defensive copy so callers can't mutate the cache.
            return RuntimeSettings(
                enabled_detectors=set(self._cache.enabled_detectors)
            )

    def update(self, *, enabled_detectors: set[str]) -> RuntimeSettings:
        # Keep only known kinds so we never persist nonsense entries.
        sanitized = {k for k in enabled_detectors if k in ALL_KINDS}
        with self._lock:
            self._cache = RuntimeSettings(enabled_detectors=sanitized)
            self._save(self._cache)
            return RuntimeSettings(enabled_detectors=set(sanitized))

    def get_enabled_kinds(self) -> set[str]:
        """Convenience hook used by the augmented client."""
        return self.get().enabled_detectors

    def _load(self) -> RuntimeSettings:
        if not self._path.exists():
            return RuntimeSettings()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return RuntimeSettings.from_dict(data)
        except Exception:
            # Corrupt file — fall back to defaults rather than crash startup.
            return RuntimeSettings()

    def _save(self, settings: RuntimeSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(settings.to_dict(), indent=2),
            encoding="utf-8",
        )
