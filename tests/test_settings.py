"""Tests for the runtime settings store and the /settings endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anonymizer_api.config import Settings
from anonymizer_api.main import create_app
from anonymizer_api.settings_store import (
    ALL_KINDS,
    DEFAULT_ENABLED,
    SettingsStore,
)


# ---------------------------------------------------------------------------
# SettingsStore unit tests
# ---------------------------------------------------------------------------

class TestSettingsStore:
    def test_default_when_file_absent(self, tmp_path: Path) -> None:
        store = SettingsStore(tmp_path / "config.json")
        assert store.get().enabled_detectors == set(DEFAULT_ENABLED)

    def test_persists_on_update(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        store = SettingsStore(path)
        store.update(enabled_detectors={"cpf", "cnpj"})

        # New store instance reads from disk
        store2 = SettingsStore(path)
        assert store2.get().enabled_detectors == {"cpf", "cnpj"}

    def test_unknown_kinds_filtered(self, tmp_path: Path) -> None:
        store = SettingsStore(tmp_path / "config.json")
        result = store.update(enabled_detectors={"cpf", "made_up_kind"})
        assert "made_up_kind" not in result.enabled_detectors
        assert "cpf" in result.enabled_detectors

    def test_get_returns_copy(self, tmp_path: Path) -> None:
        store = SettingsStore(tmp_path / "config.json")
        snap = store.get()
        snap.enabled_detectors.discard("cpf")
        # Cache should be untouched
        assert "cpf" in store.get().enabled_detectors

    def test_corrupt_file_falls_back_to_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text("{not valid json", encoding="utf-8")
        store = SettingsStore(path)
        assert store.get().enabled_detectors == set(DEFAULT_ENABLED)


# ---------------------------------------------------------------------------
# /settings endpoints
# ---------------------------------------------------------------------------

POLICY_PATH = Path(__file__).parent.parent / "policies" / "default.yaml"


@pytest.fixture()
def api_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        quarantine_dir=tmp_path / "q",
        output_dir=tmp_path / "out",
        db_url=f"sqlite:///{tmp_path}/api.db",
        runtime_config_path=tmp_path / "runtime.json",
        max_bytes=1 * 1024 * 1024,
        policy_path=POLICY_PATH,
        use_mock_client=True,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_get_returns_full_catalogue(api_client: TestClient) -> None:
    r = api_client.get("/settings")
    assert r.status_code == 200
    body = r.json()
    assert set(body["available_detectors"]) == set(ALL_KINDS)
    assert "cpf" in body["enabled_detectors"]


def test_put_updates_persists(api_client: TestClient) -> None:
    r = api_client.put(
        "/settings",
        json={"enabled_detectors": ["cpf", "cnpj", "private_email"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert sorted(body["enabled_detectors"]) == ["cnpj", "cpf", "private_email"]

    # GET reflects the change
    r2 = api_client.get("/settings")
    assert sorted(r2.json()["enabled_detectors"]) == [
        "cnpj",
        "cpf",
        "private_email",
    ]


def test_put_drops_unknown_kinds(api_client: TestClient) -> None:
    r = api_client.put(
        "/settings",
        json={"enabled_detectors": ["cpf", "totally_invented"]},
    )
    assert r.status_code == 200
    assert "totally_invented" not in r.json()["enabled_detectors"]


def test_disabled_detector_does_not_appear_in_pipeline(
    api_client: TestClient, tmp_path: Path
) -> None:
    """End-to-end: disable 'cpf' → uploaded doc with a CPF doesn't get it
    classified as cpf."""
    # Disable cpf detection
    api_client.put(
        "/settings",
        json={
            "enabled_detectors": [
                k
                for k in ALL_KINDS
                if k != "cpf"
            ]
        },
    )

    # Upload a doc with a CPF
    files = {"file": ("doc.txt", b"CPF: 111.444.777-35", "text/plain")}
    r = api_client.post("/jobs/upload", files=files)
    job_id = r.json()["job_id"]

    # Wait for completion
    import time
    for _ in range(30):
        r = api_client.get(f"/jobs/{job_id}")
        if r.json()["status"] not in {"pending", "processing"}:
            break
        time.sleep(0.05)

    report = api_client.get(f"/jobs/{job_id}/report").json()
    # No span should be tagged as cpf
    cpf_spans = [
        s for s in report["applied_spans"] if s["entity_type"] == "cpf"
    ]
    assert cpf_spans == []
