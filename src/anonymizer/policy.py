from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import Strategy
from .risk import VerificationConfig


class EntityPolicy:
    def __init__(self, entity_type: str, config: dict[str, Any]) -> None:
        self.entity_type = entity_type
        self.strategy: Strategy = config["strategy"]
        self.label: str = config.get("label", f"[{entity_type.upper()}]")
        self.mask_char: str = config.get("mask_char", "*")

    def __repr__(self) -> str:
        return f"EntityPolicy(entity_type={self.entity_type!r}, strategy={self.strategy!r})"


class Policy:
    def __init__(
        self,
        entities: dict[str, EntityPolicy],
        verification: VerificationConfig | None = None,
    ) -> None:
        self._entities = entities
        self._verification = verification

    @classmethod
    def from_yaml(cls, path: Path) -> "Policy":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        entities = {
            name: EntityPolicy(name, cfg)
            for name, cfg in raw.get("entities", {}).items()
        }
        ver_raw = raw.get("verification")
        verification = VerificationConfig.from_dict(ver_raw) if ver_raw else None
        return cls(entities, verification)

    def get(self, entity_type: str) -> EntityPolicy | None:
        return self._entities.get(entity_type)

    def __contains__(self, entity_type: str) -> bool:
        return entity_type in self._entities

    @property
    def verification(self) -> VerificationConfig | None:
        return self._verification
