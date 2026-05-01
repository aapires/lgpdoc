"""End-to-end tests for the pseudonymization-containers API (Sprint 1).

Sprint 1 ships CRUD only. The tests here cover the happy paths plus the
edge cases that matter for product invariants: container IDs are UUIDs,
status transitions, and listing isolation. Sprint 2 will add tests
covering documents and the marker mapping.
"""
from __future__ import annotations

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


def _create(
    client: TestClient, *, name: str, description: str | None = None
):
    body = {"name": name}
    if description is not None:
        body["description"] = description
    return client.post("/api/containers", json=body)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class TestCreate:
    def test_creates_container_and_returns_summary(
        self, api_client: TestClient
    ) -> None:
        r = _create(api_client, name="Análise Alfa", description="case A")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "Análise Alfa"
        assert body["description"] == "case A"
        assert body["status"] == "active"
        assert body["document_count"] == 0
        assert body["marker_count"] == 0
        # UUID-shaped id
        assert len(body["container_id"]) == 36
        assert body["container_id"].count("-") == 4

    def test_create_strips_whitespace(self, api_client: TestClient) -> None:
        r = _create(api_client, name="  Caso Beta  ", description="   ")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "Caso Beta"
        # Empty description after strip becomes null
        assert body["description"] is None

    def test_create_rejects_empty_name(self, api_client: TestClient) -> None:
        r = _create(api_client, name="   ")
        assert r.status_code == 400
        assert "empty" in r.json()["detail"].lower()

    def test_create_rejects_missing_name(self, api_client: TestClient) -> None:
        r = api_client.post("/api/containers", json={})
        # Pydantic 2 returns 422 for schema-level validation errors
        assert r.status_code == 422

    def test_create_rejects_oversized_name(
        self, api_client: TestClient
    ) -> None:
        r = _create(api_client, name="x" * 250)  # max_length=200
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# List & Get
# ---------------------------------------------------------------------------

class TestListAndGet:
    def test_list_starts_empty(self, api_client: TestClient) -> None:
        r = api_client.get("/api/containers")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_returns_newest_first(self, api_client: TestClient) -> None:
        _create(api_client, name="primeiro")
        _create(api_client, name="segundo")
        _create(api_client, name="terceiro")

        r = api_client.get("/api/containers")
        assert r.status_code == 200
        names = [c["name"] for c in r.json()]
        # created_at desc → newest (last inserted) comes first
        assert names == ["terceiro", "segundo", "primeiro"]

    def test_list_filters_by_status(self, api_client: TestClient) -> None:
        cid = _create(api_client, name="ativo").json()["container_id"]
        archived_id = _create(api_client, name="arquivado").json()[
            "container_id"
        ]
        # Archive the second one
        r = api_client.patch(
            f"/api/containers/{archived_id}",
            json={"status": "archived"},
        )
        assert r.status_code == 200

        r = api_client.get("/api/containers", params={"status": "active"})
        assert r.status_code == 200
        ids = [c["container_id"] for c in r.json()]
        assert ids == [cid]

        r = api_client.get("/api/containers", params={"status": "archived"})
        ids = [c["container_id"] for c in r.json()]
        assert ids == [archived_id]

    def test_list_rejects_unknown_status(self, api_client: TestClient) -> None:
        r = api_client.get("/api/containers", params={"status": "bogus"})
        assert r.status_code == 400

    def test_get_returns_single_container(
        self, api_client: TestClient
    ) -> None:
        created = _create(api_client, name="X").json()
        cid = created["container_id"]

        r = api_client.get(f"/api/containers/{cid}")
        assert r.status_code == 200
        assert r.json()["container_id"] == cid

    def test_get_404_for_missing(self, api_client: TestClient) -> None:
        r = api_client.get("/api/containers/does-not-exist")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

class TestUpdate:
    def test_partial_update_changes_only_provided_fields(
        self, api_client: TestClient
    ) -> None:
        cid = _create(
            api_client, name="orig", description="orig desc"
        ).json()["container_id"]

        r = api_client.patch(
            f"/api/containers/{cid}", json={"name": "novo nome"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "novo nome"
        # Description must NOT have been overwritten
        assert body["description"] == "orig desc"

    def test_update_archives_container(self, api_client: TestClient) -> None:
        cid = _create(api_client, name="X").json()["container_id"]
        r = api_client.patch(
            f"/api/containers/{cid}", json={"status": "archived"}
        )
        assert r.status_code == 200
        assert r.json()["status"] == "archived"

    def test_update_rejects_invalid_status(
        self, api_client: TestClient
    ) -> None:
        cid = _create(api_client, name="X").json()["container_id"]
        r = api_client.patch(
            f"/api/containers/{cid}", json={"status": "bogus"}
        )
        # Pydantic enforces the Literal at schema validation time → 422.
        assert r.status_code == 422

    def test_update_rejects_empty_name(self, api_client: TestClient) -> None:
        cid = _create(api_client, name="X").json()["container_id"]
        r = api_client.patch(f"/api/containers/{cid}", json={"name": "   "})
        # The service strips and rejects whitespace-only names → 400.
        # (Pydantic's ``min_length=1`` only catches the empty string;
        # all-whitespace passes the schema and falls through to the
        # service's ``ContainerValidationError``.)
        assert r.status_code == 400
        assert "empty" in r.json()["detail"].lower()

    def test_update_404_for_missing(self, api_client: TestClient) -> None:
        r = api_client.patch(
            "/api/containers/missing", json={"name": "x"}
        )
        assert r.status_code == 404

    def test_empty_patch_is_noop(self, api_client: TestClient) -> None:
        cid = _create(api_client, name="X").json()["container_id"]
        r = api_client.patch(f"/api/containers/{cid}", json={})
        assert r.status_code == 200
        assert r.json()["name"] == "X"

    def test_clearing_description_with_empty_string(
        self, api_client: TestClient
    ) -> None:
        cid = _create(
            api_client, name="X", description="desc"
        ).json()["container_id"]
        r = api_client.patch(
            f"/api/containers/{cid}", json={"description": "   "}
        )
        assert r.status_code == 200
        assert r.json()["description"] is None


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_returns_204_and_removes_row(
        self, api_client: TestClient
    ) -> None:
        cid = _create(api_client, name="X").json()["container_id"]
        r = api_client.delete(f"/api/containers/{cid}")
        assert r.status_code == 204
        # Subsequent GET → 404
        r = api_client.get(f"/api/containers/{cid}")
        assert r.status_code == 404

    def test_delete_404_for_missing(self, api_client: TestClient) -> None:
        r = api_client.delete("/api/containers/missing")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Architectural invariants — the new feature must not contaminate JobService
# ---------------------------------------------------------------------------

class TestArchitecturalIsolation:
    def test_container_routes_live_outside_jobs_router(
        self, api_client: TestClient
    ) -> None:
        """A path like ``/api/containers`` must NOT be a sub-route of
        ``/jobs``. The two domains share an app but never a router."""
        r = api_client.get("/jobs/api/containers")
        # Either 404 or 405 — both mean the path is not served by /jobs.
        assert r.status_code in (404, 405)

    def test_job_service_does_not_import_container_module(self) -> None:
        """JobService must not IMPORT from anonymizer_api.containers —
        comments and lifecycle-hook references in docstrings are fine
        (they describe how the integration works without coupling)."""
        from anonymizer_api.jobs import service as job_service_mod

        src = Path(job_service_mod.__file__).read_text(encoding="utf-8")
        # Reject any actual import dependency.
        assert "from anonymizer_api.containers" not in src
        assert "from ..containers" not in src
        assert "import anonymizer_api.containers" not in src

    def test_existing_modes_still_listed(self, api_client: TestClient) -> None:
        """Sanity: the three pre-existing modes' endpoints continue to
        respond. A regression here would mean adding containers broke
        the app factory."""
        # /jobs (anonymization + reversible + comparison live here)
        r = api_client.get("/jobs")
        assert r.status_code == 200
        # /settings (detector toggles)
        r = api_client.get("/settings")
        assert r.status_code == 200
        # /health
        r = api_client.get("/health")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Logging discipline — descriptions can carry sensitive notes; never log them
# ---------------------------------------------------------------------------

class TestLoggingPrivacy:
    def test_create_does_not_log_description_text(
        self,
        api_client: TestClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        sentinel = "SENTINEL_DESC_4242_CONFIDENTIAL"
        with caplog.at_level(logging.DEBUG):
            r = _create(
                api_client,
                name="case-X",
                description=f"context with {sentinel} inside",
            )
            assert r.status_code == 201
        for record in caplog.records:
            assert sentinel not in record.getMessage(), (
                f"Container service leaked the description body via "
                f"logger={record.name!r}: {record.getMessage()!r}"
            )

    def test_create_does_not_log_name_text(
        self,
        api_client: TestClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Names are typically not PII (they're case codes), but the
        service's logging discipline still says metadata-only — only
        ``len(name)`` should hit the logs."""
        import logging

        sentinel = "ZZZ_NAME_SENTINEL_888"
        with caplog.at_level(logging.DEBUG):
            r = _create(api_client, name=sentinel)
            assert r.status_code == 201
        for record in caplog.records:
            assert sentinel not in record.getMessage()
