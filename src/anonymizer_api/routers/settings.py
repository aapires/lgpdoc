"""GET / PUT /settings — runtime configuration of which detectors are active."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..schemas import RuntimeSettingsSchema, SettingsCatalogue
from ..settings_store import ALL_KINDS, SettingsStore

router = APIRouter(prefix="/settings", tags=["settings"])


def _get_store(request: Request) -> SettingsStore:
    return request.app.state.settings_store


@router.get("", response_model=SettingsCatalogue)
def get_settings(
    store: SettingsStore = Depends(_get_store),
) -> SettingsCatalogue:
    settings = store.get()
    return SettingsCatalogue(
        enabled_detectors=sorted(settings.enabled_detectors),
        available_detectors=list(ALL_KINDS),
    )


@router.put("", response_model=SettingsCatalogue)
def update_settings(
    body: RuntimeSettingsSchema,
    store: SettingsStore = Depends(_get_store),
) -> SettingsCatalogue:
    updated = store.update(enabled_detectors=set(body.enabled_detectors))
    return SettingsCatalogue(
        enabled_detectors=sorted(updated.enabled_detectors),
        available_detectors=list(ALL_KINDS),
    )
