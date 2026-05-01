"""Architectural invariants — keep the three operating modes from leaking
into each other.

The product has three orthogonal modes:

* **Anonymization** — irreversible redaction. Approve → download.
* **Reversible pseudonymization** — markers + restore round-trip.
* **Detector comparison** — diagnostic only. Never alters the job.

These tests fail fast if a future change blurs the boundary.
"""
from __future__ import annotations

import hashlib
import inspect
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anonymizer import detector_comparison as dc_core
from anonymizer_api.config import Settings
from anonymizer_api.jobs import service as service_mod
from anonymizer_api.main import create_app
from anonymizer_api.routers import detector_comparison as dc_router

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


def _wait(client: TestClient, job_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/jobs/{job_id}")
        assert r.status_code == 200, r.text
        if r.json()["status"] not in {"pending", "processing"}:
            return r
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not complete within {timeout}s")


def _upload(
    client: TestClient,
    content: bytes,
    *,
    mode: str = "anonymization",
    filename: str = "doc.txt",
) -> str:
    files = {"file": (filename, content, "text/plain")}
    r = client.post("/jobs/upload", files=files, data={"mode": mode})
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


def _upload_and_finish(
    client: TestClient, content: bytes, *, mode: str = "anonymization"
) -> str:
    job_id = _upload(client, content, mode=mode)
    _wait(client, job_id)
    return job_id


# Synthetic doc with a labelled name + email + a bare CPF-shaped string so
# both the mock OPF and the regex side find something.
SAMPLE_DOC = (
    "Cliente: Joao Silva.\n"
    "Email: alice@example.com.\n"
    "OAB/SP 12345.\n"
)


# ---------------------------------------------------------------------------
# Static / structural invariants — verified by reading the source modules
# ---------------------------------------------------------------------------

class TestContainersIsolatedFromJobsService:
    """The container feature must live entirely in its own package and
    never reach into the jobs subsystem (or vice versa). These checks
    are verified by reading the source files."""

    def test_containers_package_does_not_import_jobs(self) -> None:
        from anonymizer_api import containers as containers_pkg

        package_dir = Path(containers_pkg.__file__).parent
        for py_file in package_dir.glob("*.py"):
            src = py_file.read_text(encoding="utf-8")
            assert "anonymizer_api.jobs" not in src, (
                f"{py_file.name} imports from anonymizer_api.jobs — the "
                f"container feature must not reuse JobService internals."
            )
            assert "from ..jobs" not in src, py_file.name

    def test_jobs_service_does_not_import_containers(self) -> None:
        """JobService stays ignorant of containers at the *import*
        level. Mentions in comments / docstrings are fine — what we
        guard is the dependency direction."""
        from anonymizer_api.jobs import service as job_service_mod

        src = Path(job_service_mod.__file__).read_text(encoding="utf-8")
        assert "from anonymizer_api.containers" not in src
        assert "from ..containers" not in src
        assert "import anonymizer_api.containers" not in src


class TestComparisonNeverUsesCompositeClient:
    """The diagnostic mode is meaningless if it shares the augmented
    pipeline with production. Asserting at the source level catches
    accidental imports long before behaviour drifts."""

    def test_comparison_core_imports(self) -> None:
        src = Path(dc_core.__file__).read_text(encoding="utf-8")
        assert "CompositeClient" not in src, (
            "detector_comparison core must never reference CompositeClient"
        )
        assert "_OverridingComposite" not in src
        assert "make_augmented_client" not in src

    def test_comparison_router_imports(self) -> None:
        src = Path(dc_router.__file__).read_text(encoding="utf-8")
        assert "CompositeClient" not in src
        assert "make_augmented_client" not in src
        # The router must NOT touch redaction artefacts — that's strictly
        # production territory.
        assert "redacted_path" not in src
        assert "spans_path" not in src

    def test_comparison_service_method_uses_only_provided_clients(self) -> None:
        """``run_detector_comparison`` must accept opf and regex clients
        as parameters and not reach into ``self.client`` (the augmented
        production client). Reading the source guarantees the method
        body has no ``self.client`` reference."""
        src = inspect.getsource(service_mod.JobService.run_detector_comparison)
        assert "self.client" not in src, (
            "Comparison service must not consume the augmented production "
            "client; it receives the pure OPF and regex clients via params."
        )
        # Sanity: the method DOES use the parameters it receives.
        assert "opf_client.detect" in src
        assert "regex_client.detect" in src

    def test_app_state_separates_three_clients(
        self, api_settings: Settings
    ) -> None:
        """Production exposes three distinct clients on app.state, and
        the comparison-side OPF is the case-normalised wrapper, never
        the augmented one."""
        from anonymizer.augmentations import (
            CaseNormalizingClient,
            _OverridingComposite,
        )

        app = create_app(api_settings)
        with TestClient(app):
            # Production: augmented composite.
            assert isinstance(app.state.client, _OverridingComposite)
            # Diagnostic OPF side: case-normalising wrapper, NOT augmented.
            assert isinstance(app.state.opf_client, CaseNormalizingClient)
            assert not isinstance(app.state.opf_client, _OverridingComposite)
            # Regex side stays separate.
            from anonymizer.regex_only_client import RegexOnlyClient
            assert isinstance(app.state.regex_client, RegexOnlyClient)


# ---------------------------------------------------------------------------
# Behavioural invariants — comparison never modifies the job
# ---------------------------------------------------------------------------

class TestComparisonPreservesJob:
    def test_comparison_does_not_alter_redacted_artefact(
        self, api_client: TestClient, api_settings: Settings
    ) -> None:
        """POST /detector-comparison must never touch redacted.txt.
        Hash before and after to prove byte-level immutability."""
        job_id = _upload_and_finish(api_client, SAMPLE_DOC.encode("utf-8"))

        redacted_path = api_settings.output_dir / job_id / "redacted.txt"
        assert redacted_path.exists()
        before = hashlib.sha256(redacted_path.read_bytes()).hexdigest()

        r = api_client.post(f"/jobs/{job_id}/detector-comparison")
        assert r.status_code == 200, r.text

        after = hashlib.sha256(redacted_path.read_bytes()).hexdigest()
        assert (
            before == after
        ), "Comparison endpoint must not alter the redacted artefact."

    def test_comparison_does_not_alter_applied_spans(
        self, api_client: TestClient, api_settings: Settings
    ) -> None:
        job_id = _upload_and_finish(api_client, SAMPLE_DOC.encode("utf-8"))
        spans_path = api_settings.output_dir / job_id / "spans.json"
        before = spans_path.read_bytes()

        r = api_client.post(f"/jobs/{job_id}/detector-comparison")
        assert r.status_code == 200

        after = spans_path.read_bytes()
        assert before == after, "Comparison must not rewrite spans.json."

    def test_comparison_preserves_decision_and_risk(
        self, api_client: TestClient
    ) -> None:
        """Beyond ``status``, the diagnostic must not touch the
        pipeline's recommendation either."""
        job_id = _upload_and_finish(api_client, SAMPLE_DOC.encode("utf-8"))
        before = api_client.get(f"/jobs/{job_id}").json()

        api_client.post(f"/jobs/{job_id}/detector-comparison")

        after = api_client.get(f"/jobs/{job_id}").json()
        for key in ("status", "decision", "risk_level", "risk_score"):
            assert (
                before.get(key) == after.get(key)
            ), f"Comparison must not change job.{key}"

    def test_comparison_does_not_create_anonymized_download(
        self, api_client: TestClient
    ) -> None:
        """The diagnostic must not unlock a new download channel —
        only ``approve`` opens the download endpoint."""
        job_id = _upload_and_finish(api_client, SAMPLE_DOC.encode("utf-8"))
        # Pre-condition: download is gated until reviewer approves.
        r = api_client.get(f"/jobs/{job_id}/download")
        assert r.status_code == 403

        api_client.post(f"/jobs/{job_id}/detector-comparison")

        # Post-condition: still gated. The diagnostic did NOT count as a
        # release decision.
        r = api_client.get(f"/jobs/{job_id}/download")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Anonymization-mode jobs cannot restore (reversible-only)
# ---------------------------------------------------------------------------

class TestAnonymizationCannotRestore:
    def test_validate_endpoint_rejects_anonymization_job(
        self, api_client: TestClient
    ) -> None:
        """The reversible/validate endpoint must refuse jobs uploaded in
        anonymization mode — restoration depends on the marker round-trip
        which only the reversible flow produces."""
        job_id = _upload_and_finish(
            api_client, SAMPLE_DOC.encode("utf-8"), mode="anonymization"
        )
        r = api_client.post(
            f"/jobs/{job_id}/reversible/validate",
            json={"processed_text": "anything"},
        )
        assert r.status_code == 400
        assert "irreversible" in r.json()["detail"].lower()

    def test_restore_endpoint_rejects_anonymization_job(
        self, api_client: TestClient
    ) -> None:
        job_id = _upload_and_finish(
            api_client, SAMPLE_DOC.encode("utf-8"), mode="anonymization"
        )
        r = api_client.post(
            f"/jobs/{job_id}/reversible/restore",
            json={"processed_text": "anything"},
        )
        assert r.status_code == 400
        assert "irreversible" in r.json()["detail"].lower()

    def test_status_endpoint_marks_anonymization_unavailable(
        self, api_client: TestClient
    ) -> None:
        job_id = _upload_and_finish(
            api_client, SAMPLE_DOC.encode("utf-8"), mode="anonymization"
        )
        r = api_client.get(f"/jobs/{job_id}/reversible/status")
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "anonymization"
        assert body["available"] is False


# ---------------------------------------------------------------------------
# Reversible markers must be predictable and the round-trip must use
# original_text from the persisted spans.
# ---------------------------------------------------------------------------

class TestReversibleMarkersAndRestore:
    def test_markers_match_indexed_strategy(
        self, api_client: TestClient
    ) -> None:
        """The reversible mode must produce indexed placeholders shaped
        like ``[PESSOA_NN]`` / ``[EMAIL_NN]`` etc. — never an arbitrary
        substitution. This is what makes the markers "previsíveis"."""
        job_id = _upload_and_finish(
            api_client,
            SAMPLE_DOC.encode("utf-8"),
            mode="reversible_pseudonymization",
        )
        r = api_client.post(f"/jobs/{job_id}/reversible/package")
        assert r.status_code == 200
        body = r.json()
        text = body["pseudonymized_text"]

        marker_re = re.compile(r"\[[A-Z_]+_\d{2,}\]")
        markers_in_text = set(marker_re.findall(text))
        assert markers_in_text, (
            f"Pseudonymized text should contain at least one indexed marker; "
            f"got: {text!r}"
        )
        # Every placeholder advertised by the package must appear in
        # the text and follow the indexed shape.
        for ph in body["placeholders"]:
            assert marker_re.fullmatch(ph["placeholder"]), ph["placeholder"]
            assert ph["placeholder"] in text

    def test_restore_uses_original_text_from_spans(
        self, api_client: TestClient, api_settings: Settings
    ) -> None:
        """Restoration must replace every marker with the corresponding
        ``original_text`` recorded on the span at detection time."""
        job_id = _upload_and_finish(
            api_client,
            SAMPLE_DOC.encode("utf-8"),
            mode="reversible_pseudonymization",
        )

        # Read the persisted spans.json to know what the originals are.
        import json
        spans_path = api_settings.output_dir / job_id / "spans.json"
        spans = json.loads(spans_path.read_text(encoding="utf-8"))
        # Map marker → original_text from disk; ignore false positives /
        # spans without an original_text (defensive).
        expected: dict[str, str] = {}
        for s in spans:
            if s.get("false_positive"):
                continue
            ph = s.get("replacement")
            orig = s.get("original_text")
            if ph and orig:
                expected.setdefault(ph, orig)
        assert expected, "Test setup: no markers/originals found on disk."

        # Round-trip: get package → send the same pseudonymized text back
        # to /restore and check every marker turned into its original.
        pkg = api_client.post(f"/jobs/{job_id}/reversible/package").json()
        r = api_client.post(
            f"/jobs/{job_id}/reversible/restore",
            json={"processed_text": pkg["pseudonymized_text"]},
        )
        assert r.status_code == 200, r.text
        restored = r.json()["restored_text"]

        for marker, original in expected.items():
            assert marker not in restored, (
                f"Marker {marker!r} survived restoration — restore must "
                f"replace every advertised marker with its original_text."
            )
            assert original in restored, (
                f"Restored text is missing the original {original!r} for "
                f"marker {marker!r}."
            )
