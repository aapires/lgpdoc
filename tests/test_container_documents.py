"""End-to-end tests for the container document review-then-promote flow.

Sprint 5 reroutes raw uploads through the regular jobs subsystem so the
user reviews false positives / negatives before the spans are promoted
into the container's marker table. The flow is now:

    upload → processing → pending_review → (reviewer approves) → ready

Critical invariants:

* No mapping entries exist until the reviewer approves.
* After approval the markers follow the container's global table —
  same person across two documents shares one ``[PESSOA_NNNN]``.
* Two containers stay isolated even when their markers happen to
  collide textually.
* The pseudonymised text saved against the container does NOT contain
  any of the original PII.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anonymizer_api.config import Settings
from anonymizer_api.main import create_app

POLICY_PATH = Path(__file__).parent.parent / "policies" / "default.yaml"


# Doc that exercises both name and email detection in the mock client,
# with the name appearing twice so marker reuse can be verified.
DOC_TEMPLATE = (
    "Cliente: {name}.\n"
    "Email: {email}.\n"
    "Reapresentação: {name} confirmou o envio.\n"
)


@pytest.fixture()
def api_settings(tmp_path: Path) -> Settings:
    return Settings(
        quarantine_dir=tmp_path / "quarantine",
        output_dir=tmp_path / "output",
        db_url=f"sqlite:///{tmp_path}/api.db",
        max_bytes=1 * 1024 * 1024,
        policy_path=POLICY_PATH,
        runtime_config_path=tmp_path / "runtime.json",
        # The fallback client (when OPF is OFF) is the regex mock so
        # tests still detect names without enabling the subprocess.
        use_mock_client=False,
        opf_use_mock_worker=True,
    )


@pytest.fixture()
def api_client(api_settings: Settings) -> TestClient:
    app = create_app(api_settings)
    with TestClient(app) as client:
        yield client


def _create_container(client: TestClient, name: str = "test") -> str:
    r = client.post("/api/containers", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["container_id"]


def _upload_raw(
    client: TestClient,
    container_id: str,
    *,
    body: str,
    filename: str = "doc.txt",
):
    files = {"file": (filename, body.encode("utf-8"), "text/plain")}
    return client.post(
        f"/api/containers/{container_id}/documents/raw",
        files=files,
    )


def _wait_for_status(
    client: TestClient,
    container_id: str,
    document_id: str,
    expected: str,
    *,
    timeout: float = 5.0,
) -> dict:
    """Poll the container document until its status matches ``expected``
    (or fail after ``timeout``). Used because the upload kicks the
    pipeline as a BackgroundTask — the response returns before the
    transition to ``pending_review``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(
            f"/api/containers/{container_id}/documents/{document_id}"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] == expected:
            return body
        time.sleep(0.05)
    raise AssertionError(
        f"Document {document_id} did not reach status {expected!r} within "
        f"{timeout}s — last status: {body['status']!r}"
    )


def _upload_review_approve(
    client: TestClient,
    container_id: str,
    *,
    body: str,
    filename: str = "doc.txt",
) -> dict:
    """Convenience helper: upload → wait for pending_review → approve.

    Returns the final container-document JSON (status=ready)."""
    r = _upload_raw(client, container_id, body=body, filename=filename)
    assert r.status_code == 201, r.text
    doc = r.json()
    _wait_for_status(client, container_id, doc["document_id"], "pending_review")
    r = client.post(f"/jobs/{doc['job_id']}/approve", json={})
    assert r.status_code == 200, r.text
    return _wait_for_status(
        client, container_id, doc["document_id"], "ready"
    )


# ---------------------------------------------------------------------------
# Lifecycle: upload → pending_review → approve → ready
# ---------------------------------------------------------------------------

class TestRawDocumentLifecycle:
    def test_upload_lands_in_processing_then_pending_review(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client, "test")
        r = _upload_raw(
            api_client,
            cid,
            body=DOC_TEMPLATE.format(
                name="Joao Silva", email="alice@example.com"
            ),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        # Upload is async — initial status is processing or already
        # pending_review depending on how fast the background task ran.
        assert body["status"] in {"processing", "pending_review"}
        assert body["job_id"] is not None
        assert body["source_type"] == "raw_sensitive_document"

        # Eventually settles in pending_review (the doc never auto-promotes).
        final = _wait_for_status(
            api_client, cid, body["document_id"], "pending_review"
        )
        assert final["status"] == "pending_review"

    def test_no_mapping_entries_before_approval(
        self, api_client: TestClient
    ) -> None:
        """The container's marker table must remain empty while the
        document is awaiting review — promotion only fires on approve."""
        cid = _create_container(api_client, "test")
        r = _upload_raw(
            api_client,
            cid,
            body=DOC_TEMPLATE.format(
                name="Joao Silva", email="alice@example.com"
            ),
        )
        doc = r.json()
        _wait_for_status(api_client, cid, doc["document_id"], "pending_review")

        rows = api_client.get(f"/api/containers/{cid}/mapping").json()
        assert rows == [], (
            "Mapping must be empty until the reviewer approves the job."
        )

    def test_approve_promotes_to_ready_with_mapping_entries(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client, "test")
        ready = _upload_review_approve(
            api_client,
            cid,
            body=DOC_TEMPLATE.format(
                name="Joao Silva", email="alice@example.com"
            ),
        )
        assert ready["status"] == "ready"

        rows = api_client.get(f"/api/containers/{cid}/mapping").json()
        labels = {row["marker"].split("_")[0].lstrip("[") for row in rows}
        assert "PESSOA" in labels
        assert "EMAIL" in labels
        # Counts on the container summary reflect post-promotion state.
        summary = api_client.get(f"/api/containers/{cid}").json()
        assert summary["marker_count"] == len(rows)
        assert summary["document_count"] == 1

    def test_reject_marks_document_rejected(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client, "test")
        r = _upload_raw(
            api_client,
            cid,
            body=DOC_TEMPLATE.format(
                name="Joao Silva", email="alice@example.com"
            ),
        )
        doc = r.json()
        _wait_for_status(api_client, cid, doc["document_id"], "pending_review")

        r = api_client.post(f"/jobs/{doc['job_id']}/reject", json={})
        assert r.status_code == 200, r.text

        final = _wait_for_status(
            api_client, cid, doc["document_id"], "rejected"
        )
        assert final["status"] == "rejected"
        # Reject does NOT populate mapping — only approve promotes.
        rows = api_client.get(f"/api/containers/{cid}/mapping").json()
        assert rows == []


# ---------------------------------------------------------------------------
# Marker reuse after approval
# ---------------------------------------------------------------------------

class TestMarkerReuseAfterApproval:
    def test_same_value_repeated_in_doc_uses_one_marker(
        self,
        api_client: TestClient,
        api_settings: Settings,
    ) -> None:
        cid = _create_container(api_client, "test")
        ready = _upload_review_approve(
            api_client,
            cid,
            body=DOC_TEMPLATE.format(
                name="Joao Silva", email="alice@example.com"
            ),
        )
        document_id = ready["document_id"]

        rows = api_client.get(f"/api/containers/{cid}/mapping").json()
        person_markers = [
            row["marker"]
            for row in rows
            if row["entity_type"] == "private_person"
        ]
        assert len(person_markers) == 1, (
            f"Two occurrences of the same name should yield ONE mapping "
            f"entry; got {person_markers}"
        )

        # The pseudonymized artefact saved against the container has the
        # marker repeated — never the original.
        pseudo_path = (
            api_settings.output_dir
            / "containers"
            / cid
            / f"{document_id}.pseudonymized.txt"
        )
        text = pseudo_path.read_text(encoding="utf-8")
        assert "Joao Silva" not in text
        marker = person_markers[0]
        assert marker.startswith("[PESSOA_")
        assert text.count(marker) >= 2

    def test_two_documents_share_marker(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client, "test")
        _upload_review_approve(
            api_client,
            cid,
            body=DOC_TEMPLATE.format(
                name="Joao Silva", email="alice@example.com"
            ),
            filename="doc1.txt",
        )
        _upload_review_approve(
            api_client,
            cid,
            body="Cliente: Joao Silva confirmou.\nEmail: bob@example.com.",
            filename="doc2.txt",
        )

        rows = api_client.get(f"/api/containers/{cid}/mapping").json()
        # Same person across both documents → one mapping entry
        person = [r for r in rows if r["entity_type"] == "private_person"]
        assert len(person) == 1
        # Two distinct emails → two mapping entries
        emails = [r for r in rows if r["entity_type"] == "private_email"]
        assert len(emails) == 2


# ---------------------------------------------------------------------------
# Container isolation across approvals
# ---------------------------------------------------------------------------

class TestContainerIsolationAfterApproval:
    def test_two_containers_get_independent_markers(
        self, api_client: TestClient
    ) -> None:
        cid_a = _create_container(api_client, "alpha")
        cid_b = _create_container(api_client, "beta")
        for cid in (cid_a, cid_b):
            _upload_review_approve(
                api_client,
                cid,
                body=DOC_TEMPLATE.format(
                    name="Joao Silva", email="alice@example.com"
                ),
            )

        rows_a = api_client.get(f"/api/containers/{cid_a}/mapping").json()
        rows_b = api_client.get(f"/api/containers/{cid_b}/mapping").json()
        markers_a = [
            r["marker"] for r in rows_a if r["entity_type"] == "private_person"
        ]
        markers_b = [
            r["marker"] for r in rows_b if r["entity_type"] == "private_person"
        ]
        # Both containers got [PESSOA_0001] for their first person —
        # same string, different containers, distinct identifiers.
        assert markers_a == ["[PESSOA_0001]"]
        assert markers_b == ["[PESSOA_0001]"]

    def test_mapping_endpoint_only_returns_own_container(
        self, api_client: TestClient
    ) -> None:
        cid_a = _create_container(api_client, "alpha")
        cid_b = _create_container(api_client, "beta")

        _upload_review_approve(
            api_client,
            cid_a,
            body="Cliente: Alice Costa.\n",
        )
        _upload_review_approve(
            api_client,
            cid_b,
            body="Cliente: Bob Mendes.\n",
        )

        a_rows = api_client.get(f"/api/containers/{cid_a}/mapping").json()
        b_rows = api_client.get(f"/api/containers/{cid_b}/mapping").json()
        a_originals = {row["original_text"] for row in a_rows}
        b_originals = {row["original_text"] for row in b_rows}
        assert any("Alice" in o for o in a_originals)
        assert not any("Bob" in o for o in a_originals)
        assert any("Bob" in o for o in b_originals)
        assert not any("Alice" in o for o in b_originals)


# ---------------------------------------------------------------------------
# Container-bound jobs are hidden from the regular /jobs view
# ---------------------------------------------------------------------------

class TestMappingOccurrences:
    """The mapping endpoint surfaces, per entry, the documents where the
    marker was observed. Sourced primarily from ContainerSpan rows
    (created at promotion time)."""

    def test_single_doc_marker_lists_one_occurrence(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client, "occ")
        ready = _upload_review_approve(
            api_client,
            cid,
            body=DOC_TEMPLATE.format(
                name="Joao Silva", email="alice@example.com"
            ),
            filename="caso.txt",
        )
        rows = api_client.get(f"/api/containers/{cid}/mapping").json()
        for row in rows:
            assert "occurrences" in row, "schema must expose occurrences"
            occ = row["occurrences"]
            # All markers from this single doc — exactly one occurrence each.
            assert len(occ) == 1
            assert occ[0]["filename"] == "caso.txt"
            assert occ[0]["document_id"] == ready["document_id"]

    def test_marker_reused_across_two_docs_lists_both(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client, "occ")
        # Two docs — the same person appears in both, the email only
        # in the first.
        _upload_review_approve(
            api_client,
            cid,
            body=DOC_TEMPLATE.format(
                name="Joao Silva", email="alice@example.com"
            ),
            filename="primeiro.txt",
        )
        _upload_review_approve(
            api_client,
            cid,
            body="Cliente: Joao Silva.\nEmail: bob@example.com.",
            filename="segundo.txt",
        )

        rows = api_client.get(f"/api/containers/{cid}/mapping").json()
        by_marker = {r["marker"]: r for r in rows}

        # The PERSON marker should list BOTH documents.
        person_rows = [r for r in rows if r["entity_type"] == "private_person"]
        assert len(person_rows) == 1
        person_filenames = {
            o["filename"] for o in person_rows[0]["occurrences"]
        }
        assert person_filenames == {"primeiro.txt", "segundo.txt"}

        # The first email marker only appears in the first doc.
        alice_marker = next(
            r["marker"]
            for r in rows
            if r["entity_type"] == "private_email"
            and r["normalized_value"] == "alice@example.com"
        )
        alice_filenames = {
            o["filename"] for o in by_marker[alice_marker]["occurrences"]
        }
        assert alice_filenames == {"primeiro.txt"}


class TestContainerJobsHiddenFromList:
    def test_container_job_not_in_default_jobs_list(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client, "test")
        r = _upload_raw(
            api_client,
            cid,
            body=DOC_TEMPLATE.format(
                name="Joao Silva", email="alice@example.com"
            ),
        )
        doc = r.json()
        _wait_for_status(api_client, cid, doc["document_id"], "pending_review")

        listed = api_client.get("/jobs").json()
        listed_ids = [j["job_id"] for j in listed]
        assert doc["job_id"] not in listed_ids, (
            "Container-bound jobs must not appear in /jobs by default — "
            "they have their own UI in /containers/{id}."
        )


# ---------------------------------------------------------------------------
# Document deletion preserves mapping entries
# ---------------------------------------------------------------------------

class TestDocumentDeletion:
    def test_delete_document_keeps_mapping_entries_intact(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client, "test")
        ready = _upload_review_approve(
            api_client,
            cid,
            body=DOC_TEMPLATE.format(
                name="Joao Silva", email="alice@example.com"
            ),
        )
        before = api_client.get(f"/api/containers/{cid}/mapping").json()
        assert before, "mapping should not be empty after approval"

        r = api_client.delete(
            f"/api/containers/{cid}/documents/{ready['document_id']}"
        )
        assert r.status_code == 204
        r = api_client.get(
            f"/api/containers/{cid}/documents/{ready['document_id']}"
        )
        assert r.status_code == 404

        after = api_client.get(f"/api/containers/{cid}/mapping").json()
        assert len(after) == len(before), (
            "Sprint 2 design: dropping a document keeps the marker "
            "table intact so future detections still reuse markers."
        )


# ---------------------------------------------------------------------------
# Logging discipline — pipeline + promotion never leak originals
# ---------------------------------------------------------------------------

class TestLoggingPrivacy:
    def test_full_lifecycle_does_not_log_pii(
        self,
        api_client: TestClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        cid = _create_container(api_client, "test")
        with caplog.at_level(logging.DEBUG):
            _upload_review_approve(
                api_client,
                cid,
                body=DOC_TEMPLATE.format(
                    name="Joao Silva", email="alice@example.com"
                ),
            )

        forbidden = (
            "Joao Silva",
            "alice@example.com",
            "[PESSOA_0001]",
            "[EMAIL_0001]",
        )
        for record in caplog.records:
            msg = record.getMessage()
            for token in forbidden:
                assert token not in msg, (
                    f"Lifecycle leaked {token!r} via "
                    f"logger={record.name!r}: {msg!r}"
                )


# ---------------------------------------------------------------------------
# Pseudonymised artefact downloads (single + bundle .zip)
# ---------------------------------------------------------------------------

class TestSingleDocumentDownload:
    def test_returns_pseudonymized_text_for_ready_document(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client, "dl")
        ready = _upload_review_approve(
            api_client,
            cid,
            body=DOC_TEMPLATE.format(
                name="Joao Silva", email="alice@example.com"
            ),
        )
        r = api_client.get(
            f"/api/containers/{cid}/documents/{ready['document_id']}/download"
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/plain")
        assert "attachment" in r.headers["content-disposition"]
        assert ".pseudonymized.txt" in r.headers["content-disposition"]
        # The original PII must not be in the payload — only markers.
        body = r.text
        assert "Joao Silva" not in body
        assert "[PESSOA_" in body

    def test_409_when_document_not_ready(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client, "dl")
        r = _upload_raw(
            api_client,
            cid,
            body=DOC_TEMPLATE.format(
                name="Joao Silva", email="alice@example.com"
            ),
        )
        doc = r.json()
        # Don't approve — document stays in pending_review.
        _wait_for_status(api_client, cid, doc["document_id"], "pending_review")
        r = api_client.get(
            f"/api/containers/{cid}/documents/{doc['document_id']}/download"
        )
        assert r.status_code == 409

    def test_404_for_unknown_document(self, api_client: TestClient) -> None:
        cid = _create_container(api_client, "dl")
        r = api_client.get(
            f"/api/containers/{cid}/documents/missing/download"
        )
        assert r.status_code == 404


class TestBundleDownload:
    def test_zip_includes_every_ready_document(
        self, api_client: TestClient
    ) -> None:
        import io
        import zipfile

        cid = _create_container(api_client, "bundle")
        _upload_review_approve(
            api_client,
            cid,
            body="Cliente: Joao Silva.\n",
            filename="dep1.txt",
        )
        _upload_review_approve(
            api_client,
            cid,
            body="Cliente: Maria Costa.\n",
            filename="dep2.txt",
        )

        r = api_client.get(f"/api/containers/{cid}/download-bundle.zip")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/zip"
        assert ".zip" in r.headers["content-disposition"]

        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = sorted(zf.namelist())
            assert names == [
                "dep1.pseudonymized.txt",
                "dep2.pseudonymized.txt",
            ]
            for name in names:
                content = zf.read(name).decode("utf-8")
                # Markers should be present, originals absent.
                assert "[PESSOA_" in content
                assert "Joao Silva" not in content
                assert "Maria Costa" not in content

    def test_skips_documents_not_yet_ready(
        self, api_client: TestClient
    ) -> None:
        import io
        import zipfile

        cid = _create_container(api_client, "bundle")
        # First doc — promoted to ready
        _upload_review_approve(
            api_client,
            cid,
            body="Cliente: Joao Silva.\n",
            filename="ready.txt",
        )
        # Second doc — left in pending_review
        r = _upload_raw(
            api_client,
            cid,
            body="Cliente: Maria Costa.\n",
            filename="pending.txt",
        )
        pending_doc = r.json()
        _wait_for_status(
            api_client, cid, pending_doc["document_id"], "pending_review"
        )

        r = api_client.get(f"/api/containers/{cid}/download-bundle.zip")
        assert r.status_code == 200, r.text
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
        assert "ready.pseudonymized.txt" in names
        # Pending doc has no pseudonymised artefact yet — must be excluded.
        assert "pending.pseudonymized.txt" not in names

    def test_409_when_no_ready_documents(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client, "empty")
        # Upload but don't approve.
        r = _upload_raw(
            api_client,
            cid,
            body=DOC_TEMPLATE.format(
                name="Joao Silva", email="alice@example.com"
            ),
        )
        doc = r.json()
        _wait_for_status(api_client, cid, doc["document_id"], "pending_review")

        r = api_client.get(f"/api/containers/{cid}/download-bundle.zip")
        assert r.status_code == 409
        assert "documento" in r.json()["detail"].lower()

    def test_404_for_unknown_container(self, api_client: TestClient) -> None:
        r = api_client.get(
            "/api/containers/missing/download-bundle.zip"
        )
        assert r.status_code == 404

    def test_disambiguates_identical_filenames(
        self, api_client: TestClient
    ) -> None:
        """Two uploads with the same filename must end up with distinct
        names inside the zip — otherwise the second one would silently
        overwrite the first when extracted."""
        import io
        import zipfile

        cid = _create_container(api_client, "dup")
        _upload_review_approve(
            api_client,
            cid,
            body="Cliente: Joao Silva.\n",
            filename="parecer.txt",
        )
        _upload_review_approve(
            api_client,
            cid,
            body="Cliente: Maria Costa.\n",
            filename="parecer.txt",
        )

        r = api_client.get(f"/api/containers/{cid}/download-bundle.zip")
        assert r.status_code == 200, r.text
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
        assert len(names) == 2
        assert len(set(names)) == 2  # all unique
