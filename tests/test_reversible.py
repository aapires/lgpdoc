"""Tests for the reversible pseudonymization workflow."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anonymizer_api.config import Settings
from anonymizer_api.main import create_app

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


def _wait_until_complete(client: TestClient, job_id: str, timeout: float = 5.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/jobs/{job_id}")
        if r.json()["status"] not in {"pending", "processing"}:
            return r
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} never finished")


def _upload_reversible(api_client: TestClient, content: bytes) -> str:
    r = api_client.post(
        "/jobs/upload",
        files={"file": ("doc.txt", content, "text/plain")},
        data={"mode": "reversible_pseudonymization"},
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)
    return job_id


# ---------------------------------------------------------------------------
# Mode at upload time
# ---------------------------------------------------------------------------

def test_upload_default_mode_is_anonymization(api_client: TestClient) -> None:
    r = api_client.post(
        "/jobs/upload",
        files={"file": ("doc.txt", b"Lorem ipsum.", "text/plain")},
    )
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)
    body = api_client.get(f"/jobs/{job_id}").json()
    assert body["mode"] == "anonymization"


def test_upload_reversible_mode(api_client: TestClient) -> None:
    job_id = _upload_reversible(api_client, b"Email: alice@example.org")
    body = api_client.get(f"/jobs/{job_id}").json()
    assert body["mode"] == "reversible_pseudonymization"


def test_upload_invalid_mode_returns_400(api_client: TestClient) -> None:
    r = api_client.post(
        "/jobs/upload",
        files={"file": ("doc.txt", b"x", "text/plain")},
        data={"mode": "totally_invented"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /reversible/package
# ---------------------------------------------------------------------------

def test_package_returns_text_instructions_and_placeholders(
    api_client: TestClient,
) -> None:
    job_id = _upload_reversible(
        api_client, b"Contato: alice@example.org e bob@example.com"
    )
    r = api_client.post(f"/jobs/{job_id}/reversible/package")
    assert r.status_code == 200, r.text
    pkg = r.json()
    assert pkg["pseudonymized_text"]
    assert "marcadores" in pkg["instructions"].lower()
    assert len(pkg["placeholders"]) >= 1
    for p in pkg["placeholders"]:
        assert {"placeholder", "original_text", "entity_type", "occurrences"} <= p.keys()


def test_package_refuses_anonymization_mode(api_client: TestClient) -> None:
    """Anonymization-mode jobs should not expose reversible operations."""
    r = api_client.post(
        "/jobs/upload",
        files={"file": ("doc.txt", b"Email: alice@example.org", "text/plain")},
    )
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)

    r = api_client.post(f"/jobs/{job_id}/reversible/package")
    assert r.status_code == 400
    assert "irreversible" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# /reversible/validate
# ---------------------------------------------------------------------------

def test_validate_round_trip_intact(api_client: TestClient) -> None:
    """If the user simply returns the same pseudonymized text, validation
    passes."""
    job_id = _upload_reversible(
        api_client, b"Contato: alice@example.org"
    )
    pkg = api_client.post(f"/jobs/{job_id}/reversible/package").json()

    r = api_client.post(
        f"/jobs/{job_id}/reversible/validate",
        json={"processed_text": pkg["pseudonymized_text"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert body["missing"] == []
    assert body["duplicated"] == []
    assert body["unexpected"] == []


def test_validate_detects_missing(api_client: TestClient) -> None:
    job_id = _upload_reversible(
        api_client, b"Email: alice@example.org"
    )
    pkg = api_client.post(f"/jobs/{job_id}/reversible/package").json()
    placeholder = pkg["placeholders"][0]["placeholder"]

    # User accidentally removed the placeholder
    processed = pkg["pseudonymized_text"].replace(placeholder, "")
    r = api_client.post(
        f"/jobs/{job_id}/reversible/validate",
        json={"processed_text": processed},
    )
    body = r.json()
    assert body["valid"] is False
    assert any(m["placeholder"] == placeholder for m in body["missing"])


def test_validate_detects_duplicated(api_client: TestClient) -> None:
    job_id = _upload_reversible(api_client, b"Email: alice@example.org")
    pkg = api_client.post(f"/jobs/{job_id}/reversible/package").json()
    placeholder = pkg["placeholders"][0]["placeholder"]

    # Place the marker once more than the original
    processed = pkg["pseudonymized_text"] + " duplicado " + placeholder
    r = api_client.post(
        f"/jobs/{job_id}/reversible/validate",
        json={"processed_text": processed},
    )
    body = r.json()
    assert body["valid"] is False
    assert any(d["placeholder"] == placeholder for d in body["duplicated"])


def test_validate_detects_unexpected(api_client: TestClient) -> None:
    job_id = _upload_reversible(api_client, b"Email: alice@example.org")
    pkg = api_client.post(f"/jobs/{job_id}/reversible/package").json()

    processed = pkg["pseudonymized_text"] + " also [SOMETHING_99]"
    body = api_client.post(
        f"/jobs/{job_id}/reversible/validate",
        json={"processed_text": processed},
    ).json()
    assert body["valid"] is False
    assert "[SOMETHING_99]" in body["unexpected"]


# ---------------------------------------------------------------------------
# /reversible/restore
# ---------------------------------------------------------------------------

def test_restore_round_trip(api_client: TestClient) -> None:
    """Original text comes back when the placeholders are intact."""
    original = b"Contato: alice@example.org no escritorio."
    job_id = _upload_reversible(api_client, original)
    pkg = api_client.post(f"/jobs/{job_id}/reversible/package").json()

    r = api_client.post(
        f"/jobs/{job_id}/reversible/restore",
        json={"processed_text": pkg["pseudonymized_text"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert "alice@example.org" in body["restored_text"]
    assert body["validation"]["valid"] is True


def test_restore_with_added_text_keeps_external_content(
    api_client: TestClient,
) -> None:
    """The 'LLM round-trip' scenario: external system adds prose around the
    placeholders. Original PII still gets restored where the placeholders
    are."""
    job_id = _upload_reversible(api_client, b"Cliente: alice@example.org")
    pkg = api_client.post(f"/jobs/{job_id}/reversible/package").json()
    placeholder = pkg["placeholders"][0]["placeholder"]

    processed = (
        f"Resumo do email: foi enviada uma mensagem de {placeholder} "
        "informando o status."
    )
    body = api_client.post(
        f"/jobs/{job_id}/reversible/restore",
        json={"processed_text": processed},
    ).json()
    assert "alice@example.org" in body["restored_text"]
    assert "Resumo do email" in body["restored_text"]


# ---------------------------------------------------------------------------
# /reversible/download
# ---------------------------------------------------------------------------

def test_download_requires_restore_first(api_client: TestClient) -> None:
    job_id = _upload_reversible(api_client, b"Email: alice@example.org")
    api_client.post(
        f"/jobs/{job_id}/approve", json={"reviewer": "alice"}
    )
    r = api_client.get(f"/jobs/{job_id}/reversible/download")
    assert r.status_code == 409
    assert "restore" in r.json()["detail"].lower()


def test_download_requires_approval(api_client: TestClient) -> None:
    job_id = _upload_reversible(api_client, b"Email: alice@example.org")
    pkg = api_client.post(f"/jobs/{job_id}/reversible/package").json()
    api_client.post(
        f"/jobs/{job_id}/reversible/restore",
        json={"processed_text": pkg["pseudonymized_text"]},
    )
    # No approval yet
    r = api_client.get(f"/jobs/{job_id}/reversible/download")
    assert r.status_code == 403


def test_download_after_approve_and_restore(api_client: TestClient) -> None:
    job_id = _upload_reversible(api_client, b"Email: alice@example.org")
    pkg = api_client.post(f"/jobs/{job_id}/reversible/package").json()
    api_client.post(
        f"/jobs/{job_id}/reversible/restore",
        json={"processed_text": pkg["pseudonymized_text"]},
    )
    api_client.post(
        f"/jobs/{job_id}/approve", json={"reviewer": "alice"}
    )
    r = api_client.get(f"/jobs/{job_id}/reversible/download")
    assert r.status_code == 200
    assert "alice@example.org" in r.text


# ---------------------------------------------------------------------------
# /reversible/status
# ---------------------------------------------------------------------------

def test_status_for_reversible_job(api_client: TestClient) -> None:
    job_id = _upload_reversible(api_client, b"Email: alice@example.org")
    body = api_client.get(f"/jobs/{job_id}/reversible/status").json()
    assert body["mode"] == "reversible_pseudonymization"
    assert body["available"] is True
    assert body["has_restored"] is False
    assert body["placeholder_count"] >= 1


def test_status_for_anonymization_job(api_client: TestClient) -> None:
    r = api_client.post(
        "/jobs/upload",
        files={"file": ("doc.txt", b"Lorem ipsum.", "text/plain")},
    )
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)
    body = api_client.get(f"/jobs/{job_id}/reversible/status").json()
    assert body["mode"] == "anonymization"
    assert body["available"] is False


def test_status_after_restore(api_client: TestClient) -> None:
    job_id = _upload_reversible(api_client, b"Email: alice@example.org")
    pkg = api_client.post(f"/jobs/{job_id}/reversible/package").json()
    api_client.post(
        f"/jobs/{job_id}/reversible/restore",
        json={"processed_text": pkg["pseudonymized_text"]},
    )
    body = api_client.get(f"/jobs/{job_id}/reversible/status").json()
    assert body["has_restored"] is True
