"""End-to-end tests for the FastAPI anonymizer service.

All tests use synthetic content and the regex MockPrivacyFilterClient — no
model download required.
"""
from __future__ import annotations

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
        max_bytes=1 * 1024 * 1024,  # 1 MiB
        policy_path=POLICY_PATH,
        use_mock_client=True,
    )


@pytest.fixture()
def api_client(api_settings: Settings) -> TestClient:
    app = create_app(api_settings)
    with TestClient(app) as client:
        yield client


def _wait_until_complete(client: TestClient, job_id: str, timeout: float = 5.0):
    """Block until the job leaves pending/processing or *timeout* elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/jobs/{job_id}")
        assert r.status_code == 200, r.text
        if r.json()["status"] not in {"pending", "processing"}:
            return r
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not complete within {timeout}s")


def _upload(client: TestClient, content: bytes, filename: str = "doc.txt"):
    files = {"file": (filename, content, "text/plain")}
    return client.post("/jobs/upload", files=files)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health(api_client: TestClient) -> None:
    r = api_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------

def test_upload_unsupported_extension_rejected(api_client: TestClient) -> None:
    r = _upload(api_client, b"binary", filename="bad.exe")
    assert r.status_code == 400
    assert "not allowed" in r.json()["detail"]


def test_upload_too_large_rejected(api_client: TestClient) -> None:
    big = b"x" * (2 * 1024 * 1024)  # 2 MiB > 1 MiB limit
    r = _upload(api_client, big, filename="big.txt")
    assert r.status_code == 400
    assert "too large" in r.json()["detail"].lower()


def test_upload_returns_job_id_and_pending_status(api_client: TestClient) -> None:
    r = _upload(api_client, b"Lorem ipsum.", filename="clean.txt")
    assert r.status_code == 202
    body = r.json()
    assert "job_id" in body
    assert len(body["job_id"]) == 36  # UUID4
    assert body["status"] in {"pending", "processing", "auto_approved"}


# ---------------------------------------------------------------------------
# Full lifecycle: clean text → auto_approved → download succeeds
# ---------------------------------------------------------------------------

def test_clean_text_recommends_auto_but_still_awaits_review(
    api_client: TestClient,
) -> None:
    """Even when the pipeline thinks the doc is safe, the reviewer must
    still confirm. ``decision`` and ``risk_level`` are kept as visual
    signals; the status always lands in awaiting_review."""
    r = _upload(api_client, b"Lorem ipsum dolor sit amet.", filename="clean.txt")
    job_id = r.json()["job_id"]

    r = _wait_until_complete(api_client, job_id)
    body = r.json()
    assert body["status"] == "awaiting_review"
    assert body["decision"] == "auto_approve"  # pipeline's recommendation
    assert body["risk_level"] == "low"          # signal: probably safe

    # Download is gated until the reviewer approves
    r = api_client.get(f"/jobs/{job_id}/download")
    assert r.status_code == 403

    # After explicit approval, download opens
    r = api_client.post(
        f"/jobs/{job_id}/approve",
        json={"reviewer": "alice", "note": "low risk, looks fine"},
    )
    assert r.status_code == 200
    r = api_client.get(f"/jobs/{job_id}/download")
    assert r.status_code == 200
    assert "Lorem ipsum" in r.text


# ---------------------------------------------------------------------------
# Critical-content document → awaiting_review (no auto-block)
# ---------------------------------------------------------------------------

def test_jwt_routes_to_manual_review_and_download_denied(
    api_client: TestClient,
) -> None:
    payload = (
        b"Token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abcdefghijxyz"
    )
    r = _upload(api_client, payload, filename="leak.txt")
    job_id = r.json()["job_id"]

    r = _wait_until_complete(api_client, job_id)
    body = r.json()
    # Critical content is flagged but routed through manual review — never auto-blocked.
    assert body["status"] == "awaiting_review"
    assert body["decision"] == "manual_review"
    assert body["risk_level"] == "critical"

    # Download is still gated until the reviewer approves.
    r = api_client.get(f"/jobs/{job_id}/download")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Verification report endpoint
# ---------------------------------------------------------------------------

def test_report_endpoint_returns_risk_assessment(api_client: TestClient) -> None:
    r = _upload(api_client, b"hello world", filename="clean.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)

    r = api_client.get(f"/jobs/{job_id}/report")
    assert r.status_code == 200
    body = r.json()
    assert "risk_assessment" in body
    assert body["risk_assessment"]["decision"] in {
        "auto_approve",
        "sample_review",
        "manual_review",
        "blocked",
    }


# ---------------------------------------------------------------------------
# Awaiting review → approve / reject flows
# ---------------------------------------------------------------------------

def _upload_to_review(api_client: TestClient) -> str:
    """Upload a doc that the mock pipeline pushes to awaiting_review.

    A JWT in the body triggers the deterministic 'jwt' rule during
    verification (weight 100 → critical → manual_review).
    """
    payload = (
        b"Auth header: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abcdefghij"
    )
    r = _upload(api_client, payload, filename="auth.txt")
    job_id = r.json()["job_id"]
    r = _wait_until_complete(api_client, job_id)
    assert r.json()["status"] == "awaiting_review", r.json()
    return job_id


def test_approve_then_download(api_client: TestClient) -> None:
    job_id = _upload_to_review(api_client)

    r = api_client.post(
        f"/jobs/{job_id}/approve",
        json={"reviewer": "alice", "note": "looks fine"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    r = api_client.get(f"/jobs/{job_id}/download")
    assert r.status_code == 200


def test_reject_blocks_download(api_client: TestClient) -> None:
    job_id = _upload_to_review(api_client)

    r = api_client.post(
        f"/jobs/{job_id}/reject",
        json={"reviewer": "bob", "note": "unsafe"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    r = api_client.get(f"/jobs/{job_id}/download")
    assert r.status_code == 403


def test_unapprove_moves_back_to_awaiting_review(api_client: TestClient) -> None:
    r = _upload(api_client, b"clean text", filename="clean.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)

    # Approve, then undo.
    r = api_client.post(f"/jobs/{job_id}/approve", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    r = api_client.post(f"/jobs/{job_id}/unapprove", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "awaiting_review"

    # Re-approve still works after undo.
    r = api_client.post(f"/jobs/{job_id}/approve", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


def test_unapprove_records_review_event(api_client: TestClient) -> None:
    r = _upload(api_client, b"clean text", filename="clean.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)
    api_client.post(f"/jobs/{job_id}/approve", json={})
    api_client.post(f"/jobs/{job_id}/unapprove", json={})

    events = api_client.get(f"/jobs/{job_id}/review-events").json()
    assert any(e["event_type"] == "approval_reverted" for e in events)


def test_unapprove_only_works_on_approved_jobs(api_client: TestClient) -> None:
    r = _upload(api_client, b"clean text", filename="clean.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)

    # Job is in awaiting_review, not approved → 409
    r = api_client.post(f"/jobs/{job_id}/unapprove", json={})
    assert r.status_code == 409


def test_unapprove_unknown_job_returns_404(api_client: TestClient) -> None:
    r = api_client.post(
        "/jobs/00000000-0000-0000-0000-000000000000/unapprove", json={}
    )
    assert r.status_code == 404


def test_approve_invalid_state_rejected(api_client: TestClient) -> None:
    """Approving an already-approved job is a 409."""
    r = _upload(api_client, b"clean text only", filename="clean.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)

    # First approval succeeds (job was awaiting_review)
    r = api_client.post(f"/jobs/{job_id}/approve", json={"reviewer": "alice"})
    assert r.status_code == 200

    # Second approval is invalid — job is now 'approved'
    r = api_client.post(f"/jobs/{job_id}/approve", json={"reviewer": "alice"})
    assert r.status_code == 409


def test_approve_unknown_job_returns_404(api_client: TestClient) -> None:
    r = api_client.post(
        "/jobs/00000000-0000-0000-0000-000000000000/approve",
        json={"reviewer": "x"},
    )
    assert r.status_code == 404


def test_get_unknown_job_returns_404(api_client: TestClient) -> None:
    r = api_client.get("/jobs/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# DB persistence sanity
# ---------------------------------------------------------------------------

def test_job_metadata_persisted(api_client: TestClient) -> None:
    r = _upload(api_client, b"Lorem ipsum.", filename="clean.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)

    r = api_client.get(f"/jobs/{job_id}")
    body = r.json()
    assert body["file_format"] == "txt"
    assert len(body["file_hash"]) == 64
    assert body["file_size"] == len(b"Lorem ipsum.")
    assert body["source_filename"] == "clean.txt"
    assert body["completed_at"] is not None


# ---------------------------------------------------------------------------
# List jobs
# ---------------------------------------------------------------------------

def test_list_jobs_returns_recent_first(api_client: TestClient) -> None:
    j1 = _upload(api_client, b"first", filename="a.txt").json()["job_id"]
    j2 = _upload(api_client, b"second", filename="b.txt").json()["job_id"]
    _wait_until_complete(api_client, j1)
    _wait_until_complete(api_client, j2)

    r = api_client.get("/jobs")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    job_ids = [j["job_id"] for j in body]
    assert j2 in job_ids and j1 in job_ids
    # j2 was uploaded later so it must appear first
    assert job_ids.index(j2) < job_ids.index(j1)


def test_list_jobs_filter_by_status(api_client: TestClient) -> None:
    j_clean = _upload(api_client, b"clean text", filename="clean.txt").json()["job_id"]
    _wait_until_complete(api_client, j_clean)

    r = api_client.get("/jobs?status=awaiting_review")
    statuses = {j["status"] for j in r.json()}
    assert statuses == {"awaiting_review"}


# ---------------------------------------------------------------------------
# Report includes redacted_text + applied_spans
# ---------------------------------------------------------------------------

def test_report_includes_redacted_text_and_spans(api_client: TestClient) -> None:
    payload = b"Contact: alice@example.org and bob@example.com"
    r = _upload(api_client, payload, filename="pii.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)

    r = api_client.get(f"/jobs/{job_id}/report")
    body = r.json()
    assert "redacted_text" in body
    assert "applied_spans" in body
    assert "alice@example.org" not in body["redacted_text"]


# ---------------------------------------------------------------------------
# Per-span review events
# ---------------------------------------------------------------------------

def test_create_review_event(api_client: TestClient) -> None:
    r = _upload(api_client, b"hello", filename="x.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)

    r = api_client.post(
        f"/jobs/{job_id}/review-events",
        json={
            "event_type": "accept",
            "span_index": 0,
            "reviewer": "alice",
            "note": "looks good",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["event_type"] == "accept"
    assert body["span_index"] == 0
    assert body["reviewer"] == "alice"


def test_review_event_with_payload(api_client: TestClient) -> None:
    r = _upload(api_client, b"sample", filename="s.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)

    r = api_client.post(
        f"/jobs/{job_id}/review-events",
        json={
            "event_type": "edit",
            "span_index": 0,
            "reviewer": "alice",
            "payload": {"replacement": "[CUSTOM]"},
        },
    )
    assert r.status_code == 201
    # payload comes back as JSON-encoded string
    assert "CUSTOM" in r.json()["payload"]


def test_review_events_listed_for_job(api_client: TestClient) -> None:
    r = _upload(api_client, b"hello", filename="x.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)

    api_client.post(
        f"/jobs/{job_id}/review-events",
        json={"event_type": "accept", "span_index": 0, "reviewer": "alice"},
    )
    api_client.post(
        f"/jobs/{job_id}/review-events",
        json={"event_type": "comment", "reviewer": "alice", "note": "ok"},
    )

    r = api_client.get(f"/jobs/{job_id}/review-events")
    assert r.status_code == 200
    events = r.json()
    # >=2 because auto_approved/blocked also leave their own event
    assert len([e for e in events if e["event_type"] in {"accept", "comment"}]) == 2


def test_review_event_unknown_job_returns_404(api_client: TestClient) -> None:
    r = api_client.post(
        "/jobs/00000000-0000-0000-0000-000000000000/review-events",
        json={"event_type": "accept", "reviewer": "x"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Critical-content document approves through normal review flow
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Manual redactions
# ---------------------------------------------------------------------------

def _setup_review_job(api_client: TestClient) -> tuple[str, dict]:
    """Upload a doc with content the auto-pipeline does NOT redact.
    Returns (job_id, report). The fixture deliberately uses code-like tokens
    so we have something the mock leaves untouched for manual selection.
    """
    payload = b"Cod-cliente: XPTO-9982 used in transaction. Ref XPTO-9982 too."
    r = _upload(api_client, payload, filename="doc.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)
    report = api_client.get(f"/jobs/{job_id}/report").json()
    return job_id, report


def test_manual_redaction_replaces_all_occurrences(api_client: TestClient) -> None:
    """Find-and-replace-all: one click anonymises every occurrence."""
    job_id, report = _setup_review_job(api_client)
    text = report["redacted_text"]
    target = "XPTO-9982"
    assert text.count(target) == 2  # fixture has two occurrences

    start = text.index(target)
    r = api_client.post(
        f"/jobs/{job_id}/manual-redactions",
        json={
            "start": start,
            "end": start + len(target),
            "entity_type": "account_number",
            "expected_text": target,
            "reviewer": "alice",
        },
    )
    assert r.status_code == 200, r.text
    new_report = r.json()
    new_text = new_report["redacted_text"]
    # Every occurrence was replaced by the same indexed placeholder
    assert target not in new_text
    assert new_text.count("[CONTA_01]") == 2
    assert new_report["manual_redaction_occurrences"] == 2


def test_manual_redaction_records_event(api_client: TestClient) -> None:
    job_id, report = _setup_review_job(api_client)
    text = report["redacted_text"]
    start = text.index("XPTO-9982")
    api_client.post(
        f"/jobs/{job_id}/manual-redactions",
        json={
            "start": start,
            "end": start + len("XPTO-9982"),
            "entity_type": "account_number",
            "reviewer": "bob",
            "note": "missed by OPF",
        },
    )
    events = api_client.get(f"/jobs/{job_id}/review-events").json()
    manual = [e for e in events if e["event_type"] == "manual_redaction"]
    assert len(manual) == 1
    assert manual[0]["reviewer"] == "bob"
    assert "missed by OPF" in (manual[0]["note"] or "")


def test_manual_redaction_dedupes_repeated_fragment(
    api_client: TestClient,
) -> None:
    """Two distinct selections of the same fragment share the same placeholder
    even across two separate API calls (e.g. user notices a third occurrence
    later)."""
    payload = b"Code: XPTO-9982. Reference XPTO-9982. Note XPTO-9982."
    r = _upload(api_client, payload, filename="doc.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)
    report = api_client.get(f"/jobs/{job_id}/report").json()
    text = report["redacted_text"]
    first_start = text.index("XPTO-9982")

    r1 = api_client.post(
        f"/jobs/{job_id}/manual-redactions",
        json={
            "start": first_start,
            "end": first_start + len("XPTO-9982"),
            "entity_type": "account_number",
            "expected_text": "XPTO-9982",
        },
    )
    assert r1.status_code == 200
    final = r1.json()["redacted_text"]
    assert "XPTO-9982" not in final
    # All three occurrences should share the same placeholder via dedupe
    import re as _re
    placeholders = _re.findall(r"\[CONTA_\d+\]", final)
    assert len(placeholders) == 3
    assert placeholders[0] == placeholders[1] == placeholders[2]


def test_manual_redaction_invalid_range(api_client: TestClient) -> None:
    job_id, _ = _setup_review_job(api_client)
    r = api_client.post(
        f"/jobs/{job_id}/manual-redactions",
        json={"start": 999_999, "end": 1_000_000, "entity_type": "private_person"},
    )
    assert r.status_code == 400


def test_manual_redaction_unknown_text_returns_400(api_client: TestClient) -> None:
    """If expected_text is not present in the document, return a clear error
    instead of silently anonymising the wrong slice."""
    job_id, _ = _setup_review_job(api_client)
    r = api_client.post(
        f"/jobs/{job_id}/manual-redactions",
        json={
            "start": 0,
            "end": 10,
            "entity_type": "private_person",
            "expected_text": "this string definitely does not appear anywhere",
        },
    )
    assert r.status_code == 400
    assert "not found" in r.json()["detail"].lower()


def test_manual_redaction_robust_against_stale_offsets(
    api_client: TestClient,
) -> None:
    """When the user has stale offsets but expected_text is correct, the
    redaction still succeeds — find-by-content does not require a precise
    range."""
    job_id, report = _setup_review_job(api_client)
    target = "XPTO-9982"
    # Submit garbage offsets but the right expected_text
    r = api_client.post(
        f"/jobs/{job_id}/manual-redactions",
        json={
            "start": 0,
            "end": 1,
            "entity_type": "account_number",
            "expected_text": target,
        },
    )
    assert r.status_code == 200
    new_text = r.json()["redacted_text"]
    assert target not in new_text


def test_manual_redaction_unknown_entity_type(api_client: TestClient) -> None:
    job_id, report = _setup_review_job(api_client)
    text = report["redacted_text"]
    r = api_client.post(
        f"/jobs/{job_id}/manual-redactions",
        json={
            "start": 0,
            "end": min(5, len(text)),
            "entity_type": "not_a_real_type",
        },
    )
    assert r.status_code == 400


def test_manual_redaction_unknown_job_returns_404(api_client: TestClient) -> None:
    r = api_client.post(
        "/jobs/00000000-0000-0000-0000-000000000000/manual-redactions",
        json={"start": 0, "end": 5, "entity_type": "private_person"},
    )
    assert r.status_code == 404


def test_revert_span_restores_original_text(api_client: TestClient) -> None:
    """Mark span as false positive → original text is back in the document."""
    payload = b"Contato: alice@example.org chamou."
    r = _upload(api_client, payload, filename="doc.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)
    report = api_client.get(f"/jobs/{job_id}/report").json()
    assert "alice@example.org" not in report["redacted_text"]

    # Find the email span
    spans = report["applied_spans"]
    email_idx = next(
        i for i, s in enumerate(spans) if s["entity_type"] == "private_email"
    )

    r = api_client.post(
        f"/jobs/{job_id}/spans/{email_idx}/revert",
        json={"reviewer": "alice", "note": "synthetic fixture"},
    )
    assert r.status_code == 200, r.text
    new_report = r.json()
    # Original text is back
    assert "alice@example.org" in new_report["redacted_text"]
    # Span is marked
    assert new_report["applied_spans"][email_idx]["false_positive"] is True
    # And the redacted_start/redacted_end now span the ORIGINAL text length
    s = new_report["applied_spans"][email_idx]
    assert (
        new_report["redacted_text"][s["redacted_start"]:s["redacted_end"]]
        == "alice@example.org"
    )


def test_revert_span_shifts_subsequent_positions(api_client: TestClient) -> None:
    """After reverting, every other span's offsets must still resolve to
    its replacement in the new text."""
    payload = b"Contato: alice@example.org e bob@example.com encerram."
    r = _upload(api_client, payload, filename="doc.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)
    report = api_client.get(f"/jobs/{job_id}/report").json()
    spans = report["applied_spans"]
    assert len(spans) >= 2

    # Revert the first email span
    r = api_client.post(
        f"/jobs/{job_id}/spans/0/revert",
        json={"reviewer": "alice"},
    )
    assert r.status_code == 200
    new_report = r.json()
    new_text = new_report["redacted_text"]

    # Every span still resolves to its replacement (or original if reverted)
    for s in new_report["applied_spans"]:
        slice_ = new_text[s["redacted_start"]:s["redacted_end"]]
        if s.get("false_positive"):
            assert slice_ == s["original_text"]
        else:
            assert slice_ == s["replacement"]


def test_revert_records_false_positive_event(api_client: TestClient) -> None:
    payload = b"Email: alice@example.org"
    r = _upload(api_client, payload, filename="doc.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)
    api_client.post(
        f"/jobs/{job_id}/spans/0/revert",
        json={"reviewer": "bob", "note": "not a real email"},
    )
    events = api_client.get(f"/jobs/{job_id}/review-events").json()
    fps = [e for e in events if e["event_type"] == "false_positive"]
    assert len(fps) == 1
    assert fps[0]["span_index"] == 0
    assert fps[0]["reviewer"] == "bob"


def test_revert_idempotent_returns_400(api_client: TestClient) -> None:
    payload = b"Email: alice@example.org"
    r = _upload(api_client, payload, filename="doc.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)
    api_client.post(f"/jobs/{job_id}/spans/0/revert", json={})
    # second call should fail
    r = api_client.post(f"/jobs/{job_id}/spans/0/revert", json={})
    assert r.status_code == 400


def test_revert_invalid_span_index_returns_404(api_client: TestClient) -> None:
    payload = b"hello world"
    r = _upload(api_client, payload, filename="doc.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)
    r = api_client.post(f"/jobs/{job_id}/spans/999/revert", json={})
    assert r.status_code == 404


def test_manual_redaction_source_marked(api_client: TestClient) -> None:
    """Manual spans should be tagged source='manual' so the UI can group
    them apart from model/regex detections."""
    job_id, report = _setup_review_job(api_client)
    text = report["redacted_text"]
    target = "XPTO-9982"
    start = text.index(target)
    r = api_client.post(
        f"/jobs/{job_id}/manual-redactions",
        json={
            "start": start,
            "end": start + len(target),
            "entity_type": "account_number",
            "expected_text": target,
        },
    )
    spans = r.json()["applied_spans"]
    manual = [s for s in spans if s.get("manual")]
    assert manual
    assert all(s.get("source") == "manual" for s in manual)


def test_manual_redaction_carries_original_text(
    api_client: TestClient,
) -> None:
    job_id, report = _setup_review_job(api_client)
    text = report["redacted_text"]
    target = "XPTO-9982"
    start = text.index(target)
    r = api_client.post(
        f"/jobs/{job_id}/manual-redactions",
        json={
            "start": start,
            "end": start + len(target),
            "entity_type": "account_number",
        },
    )
    assert r.status_code == 200
    new_spans = r.json()["applied_spans"]
    manual = [s for s in new_spans if s.get("manual")]
    assert manual
    assert manual[0]["original_text"] == target
    # Context fields are present (may be empty strings if at file edge)
    assert "original_context_before" in manual[0]
    assert "original_context_after" in manual[0]


def test_manual_redaction_keeps_existing_span_positions_consistent(
    api_client: TestClient,
) -> None:
    """After a manual redaction (which can replace many occurrences at once),
    every span's redacted_start/redacted_end must still index correctly into
    the new redacted text — no matter where it sits relative to the replaced
    occurrences."""
    payload = (
        b"E-mail: alice@example.org\n"
        b"Cod-cliente: XPTO-9982 used in transaction.\n"
        b"E-mail backup: bob@example.com\n"
        b"Outro XPTO-9982 mais."
    )
    r = _upload(api_client, payload, filename="doc.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)
    report = api_client.get(f"/jobs/{job_id}/report").json()
    text = report["redacted_text"]

    for s in report["applied_spans"]:
        assert text[s["redacted_start"]:s["redacted_end"]] == s["replacement"]

    target = "XPTO-9982"
    start = text.index(target)
    r = api_client.post(
        f"/jobs/{job_id}/manual-redactions",
        json={
            "start": start,
            "end": start + len(target),
            "entity_type": "account_number",
            "expected_text": target,
        },
    )
    assert r.status_code == 200
    new_report = r.json()
    new_text = new_report["redacted_text"]

    for s in new_report["applied_spans"]:
        assert "redacted_start" in s and "redacted_end" in s
        assert (
            new_text[s["redacted_start"]:s["redacted_end"]] == s["replacement"]
        ), f"span {s['entity_type']} misaligned after manual redaction"


# ---------------------------------------------------------------------------
# Permanent deletion
# ---------------------------------------------------------------------------

def test_delete_removes_db_rows_and_files(
    api_client: TestClient, api_settings, tmp_path: Path
) -> None:
    r = _upload(api_client, b"alice@example.org", filename="doc.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)

    # Output dir should exist after processing
    out_dir = api_settings.output_dir / job_id
    assert out_dir.exists()

    # Quarantine file too
    quarantine = next(api_settings.quarantine_dir.glob(f"{job_id}.*"))
    assert quarantine.exists()

    r = api_client.delete(f"/jobs/{job_id}")
    assert r.status_code == 204, r.text

    # Job is gone from DB
    r = api_client.get(f"/jobs/{job_id}")
    assert r.status_code == 404

    # Files cleaned up
    assert not out_dir.exists()
    assert not quarantine.exists()


def test_delete_unknown_job_returns_404(api_client: TestClient) -> None:
    r = api_client.delete("/jobs/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_delete_pending_job_returns_409(
    api_client: TestClient, api_settings, monkeypatch
) -> None:
    """A job stuck in 'processing' should refuse deletion to avoid orphans."""
    # Upload, then immediately rewrite status to 'processing' before it finishes.
    r = _upload(api_client, b"hello", filename="x.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)

    # Force the state back to 'processing' for the test
    import sqlite3
    db = sqlite3.connect(api_settings.db_url.replace("sqlite:///", ""))
    db.execute(
        "UPDATE jobs SET status='processing' WHERE job_id=?", (job_id,)
    )
    db.commit()
    db.close()

    r = api_client.delete(f"/jobs/{job_id}")
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Timestamps must include timezone
# ---------------------------------------------------------------------------

def test_timestamps_serialised_with_utc_offset(api_client: TestClient) -> None:
    r = _upload(api_client, b"hello", filename="x.txt")
    job_id = r.json()["job_id"]
    _wait_until_complete(api_client, job_id)

    body = api_client.get(f"/jobs/{job_id}").json()
    # Naive datetimes (no TZ info) cause browsers to interpret them as local
    # time, so every served timestamp must carry an explicit offset.
    for field in ("created_at", "updated_at", "completed_at"):
        v = body[field]
        assert v is not None
        assert v.endswith("+00:00") or v.endswith("Z"), (
            f"{field} missing timezone: {v!r}"
        )


def test_critical_document_approves_through_normal_review(
    api_client: TestClient,
) -> None:
    """A document with critical content (JWT) goes to awaiting_review and is
    handled by the normal approve flow — there is no separate override path."""
    payload = b"Token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abcdefghijxyz"
    r = _upload(api_client, payload, filename="leak.txt")
    job_id = r.json()["job_id"]
    r = _wait_until_complete(api_client, job_id)
    assert r.json()["status"] == "awaiting_review"
    assert r.json()["risk_level"] == "critical"

    r = api_client.post(
        f"/jobs/{job_id}/approve",
        json={"reviewer": "alice", "note": "synthetic test fixture"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    r = api_client.get(f"/jobs/{job_id}/download")
    assert r.status_code == 200
