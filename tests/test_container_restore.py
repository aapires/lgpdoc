"""End-to-end tests for restoration of pseudonymized data.

The product invariant: restoration uses ``container_id`` exclusively.
A marker that exists in another container must be reported as unknown
in this container, NEVER replaced silently. These tests pin that down.
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


def _create_container(client: TestClient, name: str = "test") -> str:
    return client.post("/api/containers", json={"name": name}).json()[
        "container_id"
    ]


def _seed_with_raw_doc(
    client: TestClient,
    cid: str,
    *,
    body: str | None = None,
    filename: str = "doc.txt",
) -> str:
    """Sprint 5 flow: upload → pending_review → approve → ready.
    Returns the ContainerDocument's document_id once promoted."""
    body = body or (
        "Cliente: Joao Silva.\n"
        "Email: alice@example.com.\n"
    )
    files = {"file": (filename, body.encode("utf-8"), "text/plain")}
    r = client.post(f"/api/containers/{cid}/documents/raw", files=files)
    assert r.status_code == 201, r.text
    doc = r.json()
    document_id = doc["document_id"]

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        d = client.get(
            f"/api/containers/{cid}/documents/{document_id}"
        ).json()
        if d["status"] == "pending_review":
            break
        time.sleep(0.05)
    r = client.post(f"/jobs/{doc['job_id']}/approve", json={})
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        d = client.get(
            f"/api/containers/{cid}/documents/{document_id}"
        ).json()
        if d["status"] == "ready":
            return document_id
        time.sleep(0.05)
    raise AssertionError("seed: doc never reached ready")


# ---------------------------------------------------------------------------
# /restore/text — basic happy path + unknown / malformed reporting
# ---------------------------------------------------------------------------

class TestRestoreText:
    def test_replaces_known_markers_with_originals(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client)
        _seed_with_raw_doc(api_client, cid)

        r = api_client.post(
            f"/api/containers/{cid}/restore/text",
            json={"processed_text": "Discussao com [PESSOA_0001]."},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "Joao Silva" in body["restored_text"]
        assert "[PESSOA_0001]" not in body["restored_text"]
        assert body["replaced_token_count"] == 1
        assert body["replaced_unique_count"] == 1
        assert body["unknown_markers"] == []
        assert body["malformed_markers"] == []
        assert body["is_clean"] is True

    def test_replaces_repeated_marker_count_matches(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client)
        _seed_with_raw_doc(api_client, cid)

        text = "[PESSOA_0001] cumprimentou [PESSOA_0001] novamente."
        body = api_client.post(
            f"/api/containers/{cid}/restore/text",
            json={"processed_text": text},
        ).json()
        assert body["replaced_token_count"] == 2  # two occurrences
        assert body["replaced_unique_count"] == 1  # one distinct marker
        assert body["restored_text"].count("Joao Silva") == 2

    def test_unknown_marker_reported_and_kept_in_text(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client)
        _seed_with_raw_doc(api_client, cid)

        text = "[PESSOA_0001] e [PESSOA_9999] entram no caso."
        body = api_client.post(
            f"/api/containers/{cid}/restore/text",
            json={"processed_text": text},
        ).json()
        # The known marker was replaced
        assert "Joao Silva" in body["restored_text"]
        # The unknown one was NOT replaced and is in the unknown list
        assert "[PESSOA_9999]" in body["restored_text"]
        assert "[PESSOA_9999]" in body["unknown_markers"]
        assert body["is_clean"] is False

    def test_malformed_marker_reported(self, api_client: TestClient) -> None:
        cid = _create_container(api_client)
        _seed_with_raw_doc(api_client, cid)

        body = api_client.post(
            f"/api/containers/{cid}/restore/text",
            json={"processed_text": "Hello [joao_silva] world"},
        ).json()
        assert "[joao_silva]" in body["malformed_markers"]
        # Malformed tokens are NEVER replaced — left as-is.
        assert "[joao_silva]" in body["restored_text"]

    def test_empty_text_yields_clean_result(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client)
        body = api_client.post(
            f"/api/containers/{cid}/restore/text",
            json={"processed_text": ""},
        ).json()
        assert body["restored_text"] == ""
        assert body["is_clean"] is True

    def test_404_for_unknown_container(self, api_client: TestClient) -> None:
        r = api_client.post(
            "/api/containers/missing/restore/text",
            json={"processed_text": "[PESSOA_0001]"},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Cross-container isolation — the central security invariant
# ---------------------------------------------------------------------------

class TestRestoreIsolation:
    def test_marker_from_other_container_is_unknown(
        self, api_client: TestClient
    ) -> None:
        """[PESSOA_0001] in container A points to "Joao Silva".
        [PESSOA_0001] in container B points to "Maria Souza".

        Restoring [PESSOA_0001] in container A MUST yield "Joao Silva"
        — never "Maria Souza", never empty, never a cross-leak."""
        cid_a = _create_container(api_client, "alpha")
        cid_b = _create_container(api_client, "beta")
        _seed_with_raw_doc(
            api_client,
            cid_a,
            body="Cliente: Joao Silva.\n",
        )
        _seed_with_raw_doc(
            api_client,
            cid_b,
            body="Cliente: Maria Souza.\n",
            filename="b.txt",
        )

        # Both containers happen to use [PESSOA_0001] for their first
        # person — but for *different* originals.
        a = api_client.post(
            f"/api/containers/{cid_a}/restore/text",
            json={"processed_text": "[PESSOA_0001]"},
        ).json()
        b = api_client.post(
            f"/api/containers/{cid_b}/restore/text",
            json={"processed_text": "[PESSOA_0001]"},
        ).json()

        assert a["restored_text"] == "Joao Silva"
        assert b["restored_text"] == "Maria Souza"
        # Cross-check: container A must NEVER produce Maria Souza
        # from its own marker.
        assert "Maria Souza" not in a["restored_text"]

    def test_marker_unique_to_other_container_is_reported_unknown(
        self, api_client: TestClient
    ) -> None:
        cid_a = _create_container(api_client, "alpha")
        cid_b = _create_container(api_client, "beta")
        _seed_with_raw_doc(
            api_client,
            cid_a,
            body="Cliente: Joao Silva.\n",
        )
        # Container B has TWO people, so [PESSOA_0002] exists there but
        # NOT in container A.
        _seed_with_raw_doc(
            api_client,
            cid_b,
            body="Cliente: Maria Souza.\nLider: Bruno Lima.\n",
            filename="b.txt",
        )

        a_body = api_client.post(
            f"/api/containers/{cid_a}/restore/text",
            json={"processed_text": "Notas sobre [PESSOA_0002]."},
        ).json()
        # [PESSOA_0002] is unknown in container A — left in the text.
        assert "[PESSOA_0002]" in a_body["unknown_markers"]
        assert "[PESSOA_0002]" in a_body["restored_text"]
        # And no cross-leak: Bruno never appears in container A's restore.
        assert "Bruno Lima" not in a_body["restored_text"]


# ---------------------------------------------------------------------------
# /restore/document/{did}
# ---------------------------------------------------------------------------

class TestRestoreDocument:
    def test_restores_document_to_original(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client)
        document_id = _seed_with_raw_doc(api_client, cid)

        r = api_client.post(
            f"/api/containers/{cid}/restore/document/{document_id}"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # The original PII must be back in the restored text.
        assert "Joao Silva" in body["restored_text"]
        assert "alice@example.com" in body["restored_text"]
        # And no markers should remain (since every detected value was
        # registered in the mapping, restore is clean).
        assert "[PESSOA_" not in body["restored_text"]
        assert "[EMAIL_" not in body["restored_text"]
        assert body["is_clean"] is True

    def test_404_for_unknown_document(
        self, api_client: TestClient
    ) -> None:
        cid = _create_container(api_client)
        r = api_client.post(
            f"/api/containers/{cid}/restore/document/00000000-0000-0000-0000-000000000000"
        )
        assert r.status_code == 404

    def test_404_for_unknown_container(
        self, api_client: TestClient
    ) -> None:
        r = api_client.post(
            "/api/containers/missing/restore/document/anything"
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Logging discipline — restoration produces sensitive output but logs
# only metadata.
# ---------------------------------------------------------------------------

class TestRestoreLoggingPrivacy:
    def test_restore_text_does_not_log_originals(
        self,
        api_client: TestClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        cid = _create_container(api_client)
        _seed_with_raw_doc(api_client, cid)

        sentinel_pii = "Joao Silva"
        sentinel_email = "alice@example.com"
        with caplog.at_level(logging.DEBUG):
            api_client.post(
                f"/api/containers/{cid}/restore/text",
                json={"processed_text": "[PESSOA_0001] [EMAIL_0001]"},
            )

        for record in caplog.records:
            msg = record.getMessage()
            assert sentinel_pii not in msg, (
                f"Restore leaked original PII via logger={record.name!r}: "
                f"{msg!r}"
            )
            assert sentinel_email not in msg

    def test_restore_document_does_not_log_originals(
        self,
        api_client: TestClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        cid = _create_container(api_client)
        document_id = _seed_with_raw_doc(api_client, cid)
        with caplog.at_level(logging.DEBUG):
            api_client.post(
                f"/api/containers/{cid}/restore/document/{document_id}"
            )
        for record in caplog.records:
            assert "Joao Silva" not in record.getMessage()
            assert "alice@example.com" not in record.getMessage()
