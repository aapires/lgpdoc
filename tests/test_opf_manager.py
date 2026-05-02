"""Tests for the OPF runtime toggle (subprocess + manager + endpoints).

Uses ``opf_use_mock_worker=True`` so the subprocess runs the regex
``MockPrivacyFilterClient`` instead of the real OPF model. The
plumbing (subprocess spawn/kill, lease/release, status state machine)
is exercised end-to-end without ``torch``/``opf`` being installed.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anonymizer.subprocess_opf_client import OPFWorkerError, SubprocessOPFClient
from anonymizer_api.config import Settings
from anonymizer_api.main import create_app
from anonymizer_api.opf_manager import OPFManager

POLICY_PATH = Path(__file__).parent.parent / "policies" / "default.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_settings(tmp_path: Path, *, mock_mode: bool = False) -> Settings:
    return Settings(
        quarantine_dir=tmp_path / "quarantine",
        output_dir=tmp_path / "output",
        db_url=f"sqlite:///{tmp_path}/api.db",
        max_bytes=1 * 1024 * 1024,
        policy_path=POLICY_PATH,
        runtime_config_path=tmp_path / "runtime.json",
        use_mock_client=mock_mode,
        opf_use_mock_worker=True,
    )


@pytest.fixture()
def api_settings(tmp_path: Path) -> Settings:
    return _make_settings(tmp_path)


@pytest.fixture()
def api_client(api_settings: Settings) -> TestClient:
    app = create_app(api_settings)
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# SubprocessOPFClient — bare subprocess plumbing
# ---------------------------------------------------------------------------

class TestSubprocessOPFClient:
    def test_lifecycle_start_detect_stop(self) -> None:
        c = SubprocessOPFClient(use_mock=True)
        c.start()
        assert c.is_running()
        spans = c.detect("Cliente: Joao Silva. Email: alice@example.com")
        assert any(s.entity_type == "private_email" for s in spans)
        c.stop()
        assert not c.is_running()

    def test_detect_before_start_raises(self) -> None:
        c = SubprocessOPFClient(use_mock=True)
        with pytest.raises(OPFWorkerError, match="not running"):
            c.detect("anything")

    def test_stop_is_idempotent(self) -> None:
        c = SubprocessOPFClient(use_mock=True)
        c.start()
        c.stop()
        c.stop()  # second call must not raise
        assert not c.is_running()

    def test_start_after_stop_works(self) -> None:
        c = SubprocessOPFClient(use_mock=True)
        c.start()
        c.stop()
        c.start()  # respawn
        assert c.is_running()
        c.stop()


# ---------------------------------------------------------------------------
# OPFManager — direct unit tests (no FastAPI layer)
# ---------------------------------------------------------------------------

class TestOPFManager:
    def test_status_when_unavailable(self) -> None:
        m = OPFManager(available=False)
        s = m.status()
        assert s.available is False
        assert s.enabled is False

    def test_enable_unavailable_raises(self) -> None:
        m = OPFManager(available=False)
        with pytest.raises(RuntimeError, match="not available"):
            m.enable()

    def test_enable_then_disable_cycle(self) -> None:
        m = OPFManager(available=True, use_mock_worker=True)
        m.enable()
        assert m.status().enabled is True
        m.disable()
        assert m.status().enabled is False

    def test_enable_is_idempotent(self) -> None:
        m = OPFManager(available=True, use_mock_worker=True)
        m.enable()
        m.enable()  # no-op
        assert m.status().enabled is True
        m.disable()

    def test_disable_idempotent_when_off(self) -> None:
        m = OPFManager(available=True, use_mock_worker=True)
        m.disable()  # never enabled — must not raise

    def test_acquire_returns_subprocess_when_enabled(self) -> None:
        m = OPFManager(available=True, use_mock_worker=True)
        m.enable()
        leased = m.acquire()
        assert isinstance(leased, SubprocessOPFClient)
        assert m.status().in_flight_jobs == 1
        m.release(leased)
        assert m.status().in_flight_jobs == 0
        m.disable()

    def test_acquire_returns_mock_when_disabled(self) -> None:
        m = OPFManager(available=True, use_mock_worker=True)
        leased = m.acquire()  # without enabling
        # Mock leases don't hold refcount.
        assert m.status().in_flight_jobs == 0
        m.release(leased)

    def test_disable_waits_for_in_flight_lease(self) -> None:
        """While a job has acquired the OPF subprocess, ``disable()``
        must wait for ``release()`` before tearing it down — that's the
        contract that keeps in-flight jobs from getting their client
        pulled out from under them."""
        m = OPFManager(available=True, use_mock_worker=True)
        m.enable()
        leased = m.acquire()
        assert m.status().in_flight_jobs == 1

        results: list[float] = []

        def disable_thread() -> None:
            t0 = time.monotonic()
            m.disable(timeout=5.0)
            results.append(time.monotonic() - t0)

        t = threading.Thread(target=disable_thread)
        t.start()

        # Give the disable thread a moment to start waiting.
        time.sleep(0.2)
        # disable() should still be running because refcount > 0.
        assert t.is_alive()
        assert m.status().enabled is False  # toggle flipped, but worker still up
        # Now release — disable() should complete promptly.
        m.release(leased)
        t.join(timeout=3.0)
        assert not t.is_alive()
        assert results and results[0] >= 0.2  # waited at least the sleep above

    def test_ensure_loaded_enables_if_off(self) -> None:
        m = OPFManager(available=True, use_mock_worker=True)
        assert m.status().enabled is False
        m.ensure_loaded()
        assert m.status().enabled is True
        m.disable()

    def test_ensure_loaded_no_op_when_on(self) -> None:
        m = OPFManager(available=True, use_mock_worker=True)
        m.enable()
        m.ensure_loaded()  # no-op
        assert m.status().enabled is True
        m.disable()


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

class TestOPFEndpoints:
    def test_status_in_normal_mode(self, api_client: TestClient) -> None:
        r = api_client.get("/api/opf/status")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        assert body["enabled"] is False
        assert body["loading"] is False

    def test_enable_then_disable(self, api_client: TestClient) -> None:
        r = api_client.post("/api/opf/enable")
        assert r.status_code == 200
        assert r.json()["enabled"] is True

        r = api_client.post("/api/opf/disable")
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    def test_enable_in_mock_mode_returns_409(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path, mock_mode=True)
        app = create_app(s)
        with TestClient(app) as c:
            assert c.get("/api/opf/status").json()["available"] is False
            r = c.post("/api/opf/enable")
            assert r.status_code == 409
            assert "mock" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Comparison auto-load
# ---------------------------------------------------------------------------

class TestComparisonAutoLoad:
    def test_comparison_flips_toggle_on(self, api_client: TestClient) -> None:
        """POSTing to /detector-comparison must auto-load OPF and leave
        the toggle ON. The user can disable it manually afterwards."""
        # Upload a doc so we have a job to compare.
        body = "Cliente: Joao Silva. Email: alice@example.com.\n"
        files = {"file": ("doc.txt", body.encode("utf-8"), "text/plain")}
        r = api_client.post(
            "/jobs/upload", files=files, data={"mode": "anonymization"}
        )
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            s = api_client.get(f"/jobs/{job_id}").json()["status"]
            if s not in ("pending", "processing"):
                break
            time.sleep(0.05)

        # Toggle starts OFF.
        assert api_client.get("/api/opf/status").json()["enabled"] is False

        r = api_client.post(f"/jobs/{job_id}/detector-comparison")
        assert r.status_code == 200, r.text

        # After comparison, toggle is ON.
        assert api_client.get("/api/opf/status").json()["enabled"] is True

    def test_comparison_in_mock_mode_returns_409(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path, mock_mode=True)
        app = create_app(s)
        with TestClient(app) as c:
            # Upload a doc using the mock client so we have a real job.
            body = "Cliente: Joao Silva.\n"
            files = {"file": ("doc.txt", body.encode("utf-8"), "text/plain")}
            r = c.post(
                "/jobs/upload", files=files, data={"mode": "anonymization"}
            )
            assert r.status_code == 202
            job_id = r.json()["job_id"]
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                st = c.get(f"/jobs/{job_id}").json()["status"]
                if st not in ("pending", "processing"):
                    break
                time.sleep(0.05)

            r = c.post(f"/jobs/{job_id}/detector-comparison")
            assert r.status_code == 409
            assert "mock" in r.json()["detail"].lower()
