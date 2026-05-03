"""Tests for the in-place job reprocess flow.

Covers the new ``POST /jobs/{job_id}/reprocess`` endpoint and the
``opf_used`` flag captured at job-processing start. Uses
``opf_use_mock_worker=True`` so the OPF subprocess runs the regex
``MockPrivacyFilterClient`` — exercises the toggle/lease path without
torch/opf installed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anonymizer_api.config import Settings
from anonymizer_api.main import create_app

POLICY_PATH = Path(__file__).parent.parent / "policies" / "default.yaml"


@pytest.fixture()
def api_settings(tmp_path: Path) -> Settings:
    return Settings(
        quarantine_dir=tmp_path / "quarantine",
        output_dir=tmp_path / "output",
        db_url=f"sqlite:///{tmp_path}/api.db",
        max_bytes=1 * 1024 * 1024,
        policy_path=POLICY_PATH,
        runtime_config_path=tmp_path / "runtime.json",
        use_mock_client=False,
        opf_use_mock_worker=True,
    )


@pytest.fixture()
def api_client(api_settings: Settings) -> TestClient:
    app = create_app(api_settings)
    with TestClient(app) as client:
        yield client


def _wait_until(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/jobs/{job_id}").json()
        if body["status"] not in ("pending", "processing"):
            return body
        time.sleep(0.05)
    raise AssertionError(
        f"job {job_id} never left pending/processing within {timeout}s"
    )


def _upload_and_finish(client: TestClient, body: bytes = None) -> dict:
    payload = body or b"Cliente: Joao Silva. Email: alice@example.com.\n"
    r = client.post(
        "/jobs/upload",
        files={"file": ("doc.txt", payload, "text/plain")},
        data={"mode": "anonymization"},
    )
    assert r.status_code == 202, r.text
    return _wait_until(client, r.json()["job_id"])


# ---------------------------------------------------------------------------
# opf_used capture
# ---------------------------------------------------------------------------

class TestOpfUsedCapture:
    def test_off_run_records_false(self, api_client: TestClient) -> None:
        """Without enabling OPF, the job ran on the regex/mock fallback —
        opf_used should persist as False (not None)."""
        body = _upload_and_finish(api_client)
        assert body["opf_used"] is False

    def test_on_run_records_true(self, api_client: TestClient) -> None:
        """After enabling OPF, the next job acquires the subprocess
        client and opf_used should persist as True."""
        r = api_client.post("/api/opf/enable")
        assert r.status_code == 200
        body = _upload_and_finish(api_client)
        assert body["opf_used"] is True

    def test_value_returned_in_listing(self, api_client: TestClient) -> None:
        body = _upload_and_finish(api_client)
        listing = api_client.get("/jobs").json()
        match = next(j for j in listing if j["job_id"] == body["job_id"])
        assert "opf_used" in match
        assert match["opf_used"] is False


# ---------------------------------------------------------------------------
# Reprocess endpoint
# ---------------------------------------------------------------------------

class TestReprocess:
    def test_reprocess_resets_and_reruns(
        self, api_client: TestClient, api_settings: Settings
    ) -> None:
        body = _upload_and_finish(api_client)
        job_id = body["job_id"]

        spans_path = api_settings.output_dir / job_id / "spans.json"
        first_spans = json.loads(spans_path.read_text(encoding="utf-8"))
        assert first_spans, "Test setup: first run should produce spans."

        r = api_client.post(f"/jobs/{job_id}/reprocess")
        assert r.status_code == 202, r.text

        after = _wait_until(api_client, job_id)
        # Status reflects a normal completed run, not a stuck pending/processing.
        assert after["status"] in (
            "awaiting_review",
            "auto_approved",
            "approved",
            "failed",
        )
        # Spans are regenerated (file rewritten).
        new_spans = json.loads(spans_path.read_text(encoding="utf-8"))
        assert new_spans, "Reprocess should regenerate spans.json."

    def test_reprocess_picks_up_current_opf_state(
        self, api_client: TestClient
    ) -> None:
        """Original run with OPF off (opf_used=False). Enable OPF, then
        reprocess — the new opf_used should flip to True."""
        first = _upload_and_finish(api_client)
        assert first["opf_used"] is False

        api_client.post("/api/opf/enable")
        r = api_client.post(f"/jobs/{first['job_id']}/reprocess")
        assert r.status_code == 202

        after = _wait_until(api_client, first["job_id"])
        assert after["opf_used"] is True

    def test_reprocess_404_for_unknown_job(self, api_client: TestClient) -> None:
        r = api_client.post("/jobs/nope-no-such-id/reprocess")
        assert r.status_code == 404

    def test_reprocess_resets_decision_and_risk(
        self, api_client: TestClient
    ) -> None:
        """After a previous run a job carries decision/risk_level. The
        reprocess endpoint must wipe those before the worker restarts —
        otherwise stale fields could leak into the new run's UI."""
        body = _upload_and_finish(api_client)
        # Spy on intermediate state by polling immediately after the
        # reprocess request returns 202 — risk should already be cleared.
        r = api_client.post(f"/jobs/{body['job_id']}/reprocess")
        assert r.status_code == 202
        intermediate = r.json()
        assert intermediate["status"] == "pending"
        assert intermediate["decision"] is None
        assert intermediate["risk_level"] is None
        assert intermediate["risk_score"] is None
        assert intermediate["opf_used"] is None

    def test_reprocess_keeps_quarantine_file(
        self, api_client: TestClient, api_settings: Settings
    ) -> None:
        body = _upload_and_finish(api_client)
        job_id = body["job_id"]
        # The quarantine file must still exist between runs — it's the
        # source. (Output dir gets cleared.)
        quarantine_files = list(
            api_settings.quarantine_dir.glob(f"{job_id}.*")
        )
        assert quarantine_files, "Test setup: quarantine file expected."

        api_client.post(f"/jobs/{job_id}/reprocess")
        _wait_until(api_client, job_id)

        still_there = list(api_settings.quarantine_dir.glob(f"{job_id}.*"))
        assert still_there, "Reprocess must preserve the quarantine file."
