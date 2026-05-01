"""End-to-end log audit — synthetic PII must never reach the loggers.

Every flow that touches document content (anonymization pipeline, manual
redaction, reversible package + restore, detector comparison) is exercised
inside ``caplog`` and the captured records are scanned for forbidden
substrings: the synthetic PII values, their substitutions, and any
fragment that could be used to reconstruct the document.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anonymizer_api.config import Settings
from anonymizer_api.main import create_app

POLICY_PATH = Path(__file__).parent.parent / "policies" / "default.yaml"


# Tokens that must not appear verbatim in any log record. They cover:
#   * full-text PII fragments
#   * marker substitutions (markers themselves are predictable; logging
#     them would still be a leak when paired with original_text elsewhere)
#   * the doc title prefix that a sloppy logger would echo if it dumped
#     blocks verbatim
FORBIDDEN_TOKENS = (
    # Fragments of the synthetic PII
    "Joao Silva",
    "alice@example.com",
    "OAB/SP 12345",
    "Cliente:",
    # Common indexed markers — if any of these leak it likely means the
    # entire substitution table was logged.
    "[PESSOA_01]",
    "[EMAIL_01]",
    "[OAB_01]",
)

SAMPLE_DOC = (
    "Cliente: Joao Silva.\n"
    "Email: alice@example.com.\n"
    "OAB/SP 12345.\n"
)


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


def _wait(client: TestClient, job_id: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/jobs/{job_id}")
        assert r.status_code == 200, r.text
        if r.json()["status"] not in {"pending", "processing"}:
            return
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not complete within {timeout}s")


def _upload(
    client: TestClient,
    *,
    mode: str = "anonymization",
    filename: str = "doc.txt",
) -> str:
    files = {"file": (filename, SAMPLE_DOC.encode("utf-8"), "text/plain")}
    r = client.post("/jobs/upload", files=files, data={"mode": mode})
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


def _assert_no_leaks(
    caplog: pytest.LogCaptureFixture, *, where: str
) -> None:
    """Scan every captured record for forbidden synthetic tokens."""
    for record in caplog.records:
        msg = record.getMessage()
        for token in FORBIDDEN_TOKENS:
            assert token not in msg, (
                f"[{where}] log record from logger={record.name!r} leaked "
                f"forbidden token {token!r}: {msg!r}"
            )


# ---------------------------------------------------------------------------
# 1. Full anonymization pipeline + approve + download
# ---------------------------------------------------------------------------

def test_anonymization_pipeline_does_not_leak(
    api_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        job_id = _upload(api_client)
        _wait(api_client, job_id)
        api_client.post(f"/jobs/{job_id}/approve", json={})
        api_client.get(f"/jobs/{job_id}/download")
        api_client.get(f"/jobs/{job_id}/report")
    _assert_no_leaks(caplog, where="anonymization_pipeline")


# ---------------------------------------------------------------------------
# 2. Manual redaction (reviewer adds a PII fragment by selection)
# ---------------------------------------------------------------------------

def test_manual_redaction_does_not_leak(
    api_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = _upload(api_client)
    _wait(api_client, job_id)
    with caplog.at_level(logging.DEBUG):
        # Apply a manual redaction targeting a synthetic fragment that
        # exists in the redacted text. The reviewer would normally pick
        # this from the UI; here we hard-code the fragment.
        r = api_client.post(
            f"/jobs/{job_id}/manual-redactions",
            json={
                "start": 0,
                "end": 0,
                "entity_type": "private_person",
                "expected_text": "Joao Silva",
            },
        )
        # Whether or not the fragment was found in the redacted text
        # depends on the mock detection — both outcomes are fine for the
        # log audit, what matters is that nothing PII leaks regardless.
        assert r.status_code in (200, 400, 409), r.text
    _assert_no_leaks(caplog, where="manual_redaction")


# ---------------------------------------------------------------------------
# 3. Reversible package + validate + restore round-trip
# ---------------------------------------------------------------------------

def test_reversible_round_trip_does_not_leak(
    api_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = _upload(api_client, mode="reversible_pseudonymization")
    _wait(api_client, job_id)
    with caplog.at_level(logging.DEBUG):
        pkg = api_client.post(f"/jobs/{job_id}/reversible/package").json()
        api_client.post(
            f"/jobs/{job_id}/reversible/validate",
            json={"processed_text": pkg["pseudonymized_text"]},
        )
        api_client.post(
            f"/jobs/{job_id}/reversible/restore",
            json={"processed_text": pkg["pseudonymized_text"]},
        )
    _assert_no_leaks(caplog, where="reversible_round_trip")


# ---------------------------------------------------------------------------
# 4. Detector comparison (POST + GET)
# ---------------------------------------------------------------------------

def test_detector_comparison_does_not_leak(
    api_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = _upload(api_client)
    _wait(api_client, job_id)
    with caplog.at_level(logging.DEBUG):
        r = api_client.post(f"/jobs/{job_id}/detector-comparison")
        assert r.status_code == 200, r.text
        api_client.get(f"/jobs/{job_id}/detector-comparison")
    _assert_no_leaks(caplog, where="detector_comparison")


# ---------------------------------------------------------------------------
# 5. Combined regression: every flow on the same job back-to-back.
#    A leak that only surfaces when state from one flow contaminates the
#    next would slip past the per-flow tests above.
# ---------------------------------------------------------------------------

def test_all_flows_combined_do_not_leak(
    api_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    rev_id = _upload(api_client, mode="reversible_pseudonymization")
    _wait(api_client, rev_id)

    with caplog.at_level(logging.DEBUG):
        # Diagnostic on a reversible job (allowed)
        api_client.post(f"/jobs/{rev_id}/detector-comparison")
        # Reversible round-trip
        pkg = api_client.post(f"/jobs/{rev_id}/reversible/package").json()
        api_client.post(
            f"/jobs/{rev_id}/reversible/restore",
            json={"processed_text": pkg["pseudonymized_text"]},
        )
        # Approve and download
        api_client.post(f"/jobs/{rev_id}/approve", json={})
        api_client.get(f"/jobs/{rev_id}/download")
    _assert_no_leaks(caplog, where="all_flows_combined")
