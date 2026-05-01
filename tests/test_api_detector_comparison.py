"""End-to-end tests for the diagnostic detector-comparison endpoints.

The whole API is built with ``use_mock_client=True`` so the OPF model is
never loaded. The mock side ("OPF puro") is the
``MockPrivacyFilterClient`` while the regex side is the real
``RegexOnlyClient`` — together they're enough to exercise the comparison
plumbing without leaving the test process.
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_settings(tmp_path: Path) -> Settings:
    return Settings(
        quarantine_dir=tmp_path / "quarantine",
        output_dir=tmp_path / "output",
        db_url=f"sqlite:///{tmp_path}/api.db",
        max_bytes=1 * 1024 * 1024,
        policy_path=POLICY_PATH,
        runtime_config_path=tmp_path / "runtime.json",
        use_mock_client=True,
    )


@pytest.fixture()
def api_client(api_settings: Settings) -> TestClient:
    app = create_app(api_settings)
    with TestClient(app) as client:
        yield client


def _wait_until_complete(client: TestClient, job_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/jobs/{job_id}")
        assert r.status_code == 200, r.text
        if r.json()["status"] not in {"pending", "processing"}:
            return r
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not complete within {timeout}s")


# Synthetic doc crafted to produce one item of each main category:
#   * "Joao Silva" → mock detects via private_person, RegexOnlyClient via
#     br_labeled_name → both with matching entity_type.
#   * "alice@example.com" → mock detects as private_email; regex side has
#     no email rule → opf_only.
#   * "OAB/SP 12345" → mock skips (not enough digits, not capitalized
#     words); regex catches via the OAB rule → regex_only.
SAMPLE_DOC = (
    "Cliente: Joao Silva.\n"
    "Email: alice@example.com.\n"
    "OAB/SP 12345.\n"
)


def _upload(client: TestClient, content: bytes, filename: str = "doc.txt"):
    files = {"file": (filename, content, "text/plain")}
    return client.post("/jobs/upload", files=files)


def _upload_and_finish(api_client: TestClient) -> str:
    r = _upload(api_client, SAMPLE_DOC.encode("utf-8"))
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)
    return job_id


# ---------------------------------------------------------------------------
# POST: generates a fresh report
# ---------------------------------------------------------------------------

class TestPostGeneratesReport:
    def test_post_returns_200_and_includes_job_id(
        self, api_client: TestClient
    ) -> None:
        job_id = _upload_and_finish(api_client)

        r = api_client.post(f"/jobs/{job_id}/detector-comparison")

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["job_id"] == job_id
        assert "summary" in body
        assert "items" in body
        assert "by_entity_type" in body
        assert body["summary"]["total"] >= 1

    def test_post_persists_artefact_to_disk(
        self, api_client: TestClient, api_settings: Settings
    ) -> None:
        job_id = _upload_and_finish(api_client)
        api_client.post(f"/jobs/{job_id}/detector-comparison")

        path = api_settings.output_dir / job_id / "detector_comparison.json"
        assert path.exists()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["job_id"] == job_id

    def test_post_yields_both_opf_only_and_regex_only(
        self, api_client: TestClient
    ) -> None:
        """The crafted SAMPLE_DOC must produce at least one of each
        comparison status that the diagnostic mode is meant to surface."""
        job_id = _upload_and_finish(api_client)
        r = api_client.post(f"/jobs/{job_id}/detector-comparison")
        body = r.json()

        statuses = {it["status"] for it in body["items"]}
        assert "both" in statuses
        assert "opf_only" in statuses
        assert "regex_only" in statuses

        # Crash-test: the matching item must show identical entity types
        # on both sides (mock private_person ↔ regex br_labeled_name).
        both_items = [it for it in body["items"] if it["status"] == "both"]
        assert both_items
        assert (
            both_items[0]["opf_span"]["entity_type"]
            == both_items[0]["regex_span"]["entity_type"]
            == "private_person"
        )

        # The OAB span only comes from the regex side.
        regex_only = [
            it for it in body["items"] if it["status"] == "regex_only"
        ]
        assert any(
            it["regex_span"]["entity_type"] == "oab" for it in regex_only
        )


# ---------------------------------------------------------------------------
# Job status is preserved
# ---------------------------------------------------------------------------

class TestJobStatusUnchanged:
    def test_status_does_not_move_after_post(
        self, api_client: TestClient
    ) -> None:
        job_id = _upload_and_finish(api_client)
        before = api_client.get(f"/jobs/{job_id}").json()
        before_status = before["status"]
        before_decision = before.get("decision")
        before_risk = before.get("risk_level")

        r = api_client.post(f"/jobs/{job_id}/detector-comparison")
        assert r.status_code == 200, r.text

        after = api_client.get(f"/jobs/{job_id}").json()
        assert after["status"] == before_status
        assert after.get("decision") == before_decision
        assert after.get("risk_level") == before_risk


# ---------------------------------------------------------------------------
# GET: return saved report / 404 if not yet generated
# ---------------------------------------------------------------------------

class TestGetReport:
    def test_get_after_post_returns_saved_report(
        self, api_client: TestClient
    ) -> None:
        job_id = _upload_and_finish(api_client)
        api_client.post(f"/jobs/{job_id}/detector-comparison")

        r = api_client.get(f"/jobs/{job_id}/detector-comparison")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["job_id"] == job_id
        assert body["summary"]["total"] >= 1

    def test_get_before_post_returns_404(self, api_client: TestClient) -> None:
        job_id = _upload_and_finish(api_client)

        r = api_client.get(f"/jobs/{job_id}/detector-comparison")
        assert r.status_code == 404
        assert "not yet generated" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Unknown job
# ---------------------------------------------------------------------------

class TestUnknownJob:
    def test_post_404_for_missing_job(self, api_client: TestClient) -> None:
        r = api_client.post(
            "/jobs/00000000-0000-0000-0000-000000000000/detector-comparison"
        )
        assert r.status_code == 404
        assert "not found" in r.json()["detail"]

    def test_get_404_for_missing_job(self, api_client: TestClient) -> None:
        r = api_client.get(
            "/jobs/00000000-0000-0000-0000-000000000000/detector-comparison"
        )
        assert r.status_code == 404
        assert "not found" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Real OPF must never be loaded by these tests
# ---------------------------------------------------------------------------

class TestNoOpfLoading:
    def test_opf_client_in_app_state_wraps_mock(
        self, api_settings: Settings
    ) -> None:
        """The diagnostic OPF side wraps the base client in
        CaseNormalizingClient (so it sees text the same way the
        production pipeline does). With ``use_mock_client=True`` the
        innermost detector must remain the mock — the real OPF model is
        never loaded in tests."""
        from anonymizer.augmentations import CaseNormalizingClient
        from anonymizer.client import MockPrivacyFilterClient

        app = create_app(api_settings)
        with TestClient(app):
            client = app.state.opf_client
            assert isinstance(client, CaseNormalizingClient)
            inner = client._inner  # noqa: SLF001 — test introspection
            assert isinstance(inner, MockPrivacyFilterClient), (
                "The case-normalising wrapper must wrap the mock when "
                "use_mock_client=True."
            )
