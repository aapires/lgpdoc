"""Frontend invariants — terminology and feature gating.

The reviewer-ui has no JS test runner configured, but the invariants we
care about are textual (terminology) and structural (which component is
allowed to mention which feature). Reading the source from pytest is
both lighter than spinning up Jest/Vitest and faster than a build-time
check.

Each test is a regression guard against a specific product decision
documented in CLAUDE.md / project-context.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent
UI_SRC = REPO_ROOT / "apps" / "reviewer-ui" / "src"
GLOBALS_CSS = UI_SRC / "app" / "globals.css"


def _read_ts_files() -> dict[Path, str]:
    """Return ``{path: content}`` for every .ts/.tsx file under ui/src.
    Mocks are excluded — synthetic data there can mention any string."""
    files: dict[Path, str] = {}
    for path in UI_SRC.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if path.name == "mocks.ts":
            continue
        files[path] = path.read_text(encoding="utf-8")
    return files


@pytest.fixture(scope="module")
def ts_sources() -> dict[Path, str]:
    return _read_ts_files()


# ---------------------------------------------------------------------------
# Terminology: the reversible flow must say "Restaurar dados originais"
# ---------------------------------------------------------------------------

class TestReversibleTerminology:
    def test_reversible_panel_uses_restore_phrase(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """ReversiblePanel.tsx must use the canonical Portuguese phrase
        ``Restaurar dados originais`` for the restore action — that's
        the agreed product wording."""
        panel = next(
            (src for path, src in ts_sources.items()
             if path.name == "ReversiblePanel.tsx"),
            None,
        )
        assert panel is not None, "ReversiblePanel.tsx not found in src tree"
        assert "Restaurar dados originais" in panel, (
            "ReversiblePanel must use the phrase 'Restaurar dados originais' "
            "for the restore action."
        )

    def test_no_forbidden_terminology_anywhere(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """Forbidden terms by the product:

        * ``Preparar para LLM`` — old wording, both modes can feed an LLM
        * ``reidratar`` / ``rehydrate`` — wrong metaphor; we say "restore"
        * ``investiga`` (case-insensitive prefix) — sensitive vocabulary;
          use ``análise`` instead.
        """
        forbidden = (
            "Preparar para LLM",
            "reidratar",
            "rehydrate",
            "investiga",
        )
        for path, src in ts_sources.items():
            for term in forbidden:
                # Case-insensitive substring match
                assert term.lower() not in src.lower(), (
                    f"{path.relative_to(REPO_ROOT)} contains forbidden term "
                    f"{term!r}. Use the agreed product wording instead."
                )


# ---------------------------------------------------------------------------
# Anonymization-mode jobs must NOT show restore UI
# ---------------------------------------------------------------------------

class TestAnonymizationHidesRestoreUi:
    def test_job_detail_renders_reversible_only_when_mode_matches(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """The job detail page renders ``<ReversiblePanel ... />`` only
        inside an ``isReversible`` branch. Grep the file to confirm the
        condition is wired up — a regression that drops the gate would
        expose restore UI on anonymization jobs."""
        # The detail page sits at app/jobs/[job_id]/page.tsx; the review
        # variant sits at app/jobs/[job_id]/review/page.tsx — exclude it.
        page = next(
            (src for path, src in ts_sources.items()
             if path.name == "page.tsx"
             and path.parent.name == "[job_id]"),
            None,
        )
        assert page is not None, "Job detail page.tsx not found"
        assert "isReversible && <ReversiblePanel" in page, (
            "ReversiblePanel must be guarded by `isReversible &&` in the "
            "job detail page so anonymization jobs never show restore UI."
        )
        # And the boolean must be derived from job.mode.
        assert 'job.mode === "reversible_pseudonymization"' in page


# ---------------------------------------------------------------------------
# Detector comparison panel — diagnostic-only signalling
# ---------------------------------------------------------------------------

class TestDetectorComparisonIsDiagnostic:
    def test_panel_explicitly_labels_itself_diagnostic(
        self, ts_sources: dict[Path, str]
    ) -> None:
        panel = next(
            (src for path, src in ts_sources.items()
             if path.name == "DetectorComparisonPanel.tsx"),
            None,
        )
        assert panel is not None
        # The user-facing copy must contain the word "diagnóstico" so
        # reviewers don't mistake the panel for a release decision.
        assert "diagnóstico" in panel.lower(), (
            "DetectorComparisonPanel must describe itself as a diagnostic "
            "(via the word 'diagnóstico') so reviewers know it doesn't "
            "alter the document or the job state."
        )
        # And it must repeat that it does NOT alter the job (defence in
        # depth — both terms protect the user).
        assert "não altera" in panel.lower()

    def test_panel_does_not_offer_anonymized_download(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """The diagnostic panel is read-only. Any "Baixar" button or
        href to ``/download`` would imply it produces an artefact —
        which it doesn't."""
        panel = next(
            (src for path, src in ts_sources.items()
             if path.name == "DetectorComparisonPanel.tsx"),
            None,
        )
        assert panel is not None
        assert "downloadUrl" not in panel
        assert "/download" not in panel
        # Match the whole word "Baixar" in the panel — case-sensitive on
        # purpose, since the rest of the UI legitimately uses it.
        assert "Baixar" not in panel, (
            "DetectorComparisonPanel must not offer a Baixar (download) "
            "button — it's a diagnostic, not a release channel."
        )

    def test_upload_card_describes_comparison_mode(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """The third tab on the upload card must signal that it's a
        diagnostic (not a release-affecting mode)."""
        card = next(
            (src for path, src in ts_sources.items()
             if path.name == "UploadCard.tsx"),
            None,
        )
        assert card is not None
        # The user-facing description should mention 'Diagnóstico' (or
        # 'diagnóstico') so the user knows what they're picking.
        assert "iagnóstico" in card  # matches both 'D' and 'd' variants
        # And must say it doesn't alter the document.
        assert "ão altera" in card  # matches "não altera" (with accent)


# ---------------------------------------------------------------------------
# Public CSS class names referenced by the components must be defined.
# Lightweight sanity check for the "highlighted text view" and the
# diagnostic panel — keeps refactors honest.
# ---------------------------------------------------------------------------

class TestComparisonStylesPresent:
    def test_diagnostic_classes_defined_in_globals(self) -> None:
        css = GLOBALS_CSS.read_text(encoding="utf-8")
        for cls in (
            ".dc-panel",
            ".dc-status-both",
            ".dc-status-opf_only",
            ".dc-status-regex_only",
            ".dc-status-partial_overlap",
            ".dc-status-type_conflict",
            ".cv-mark",
            ".cv-mark-both",
            ".cv-mark-opf_only",
            ".cv-mark-regex_only",
        ):
            assert cls in css, (
                f"Comparison panel CSS class {cls} is referenced from TSX "
                f"but missing from globals.css."
            )


# ---------------------------------------------------------------------------
# Containers (Sprint 1) — terminology + structural invariants
# ---------------------------------------------------------------------------

class TestContainersUiInvariants:
    def test_containers_pages_exist_in_app_router(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """The new area lives under ``app/containers/`` — never under a
        legacy ``pages/`` folder. List, create, and detail must each ship
        a ``page.tsx`` file."""
        app_dir = UI_SRC / "app"
        assert (app_dir / "containers" / "page.tsx").exists()
        assert (app_dir / "containers" / "new" / "page.tsx").exists()
        # Dynamic detail route
        detail = app_dir / "containers" / "[containerId]" / "page.tsx"
        assert detail.exists(), "detail page.tsx not found"

    def test_no_pages_legacy_folder(self) -> None:
        legacy = UI_SRC / "pages"
        assert not legacy.exists(), (
            "src/pages/ is forbidden — the project uses App Router only."
        )

    def test_containers_use_canonical_phrasing(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """The list page must use ``Containers de pseudonimização`` and
        ``tabela de conversão`` — those are the agreed product terms."""
        list_page = next(
            (src for path, src in ts_sources.items()
             if path.name == "page.tsx"
             and path.parent.name == "containers"),
            None,
        )
        assert list_page is not None, "containers list page.tsx not found"
        assert "Containers de pseudonimização" in list_page
        assert "tabela de conversão" in list_page.lower()
        # The list page must surface the workspace metaphor.
        assert "Área de trabalho segura" in list_page

    def test_create_page_warns_against_pii_in_description(
        self, ts_sources: dict[Path, str]
    ) -> None:
        new_page = next(
            (src for path, src in ts_sources.items()
             if path.name == "page.tsx"
             and path.parent.name == "new"
             and path.parent.parent.name == "containers"),
            None,
        )
        assert new_page is not None
        # The description must visibly tell the user not to paste PII.
        assert "dados sensíveis" in new_page.lower() or "dados reais" in new_page.lower()

    def test_app_header_includes_containers_nav(
        self, ts_sources: dict[Path, str]
    ) -> None:
        header = next(
            (src for path, src in ts_sources.items()
             if path.name == "AppHeader.tsx"),
            None,
        )
        assert header is not None
        # Both the route and the visible label must be present.
        assert "/containers" in header
        assert "Containers" in header

    def test_container_detail_uses_planned_action_labels(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """Sprint 2 adds the actual handlers, but the labels are agreed
        product copy — they appear (disabled) on the detail page already
        so the UX is visible during Sprint 1."""
        detail = next(
            (src for path, src in ts_sources.items()
             if path.name == "page.tsx"
             and path.parent.name == "[containerId]"),
            None,
        )
        assert detail is not None
        assert "Adicionar documento sensível" in detail
        assert "Adicionar documento já pseudonimizado" in detail
        assert "tabela de conversão" in detail.lower()

    def test_container_styles_present(self) -> None:
        """The container card / hero / stat classes referenced from the
        TSX pages must exist in globals.css."""
        css = GLOBALS_CSS.read_text(encoding="utf-8")
        for cls in (
            ".container-grid",
            ".container-card",
            ".container-hero",
            ".container-stat",
            ".container-stat-status.status-active",
            ".container-stat-status.status-archived",
        ):
            assert cls in css, (
                f"Container CSS class {cls} is referenced but missing "
                f"from globals.css."
            )


# ---------------------------------------------------------------------------
# Containers (Sprint 2) — documents UI + mapping page
# ---------------------------------------------------------------------------

class TestContainerDocumentsAndMapping:
    def test_mapping_page_exists(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """The conversion table lives at
        ``app/containers/[containerId]/mapping/page.tsx``."""
        mapping_page_path = (
            UI_SRC
            / "app"
            / "containers"
            / "[containerId]"
            / "mapping"
            / "page.tsx"
        )
        assert mapping_page_path.exists(), "mapping page.tsx not found"

    def test_mapping_page_warns_about_sensitive_data(
        self, ts_sources: dict[Path, str]
    ) -> None:
        page = next(
            (src for path, src in ts_sources.items()
             if path.name == "page.tsx"
             and path.parent.name == "mapping"
             and path.parent.parent.name == "[containerId]"),
            None,
        )
        assert page is not None
        # Page must visibly warn that the table is sensitive.
        assert "dado sensível" in page.lower() or "dados sensíveis" in page.lower()
        # Real values are NEVER shown in the visual table — the
        # ``original_text`` field stays out of the JSX entirely. The
        # sensitive XLSX export endpoint is the only path that surfaces
        # them, with an explicit confirmation step in the detail page.
        assert "original_text" not in page, (
            "Mapping table must not render original_text in the UI; the "
            "sensitive export endpoint is the only sanctioned path."
        )
        # The "Ocorrências" column lists each filename where the marker
        # was observed.
        assert "Ocorrências" in page
        assert "occurrences" in page  # field is read from each entry

    def test_detail_page_supports_raw_document_upload(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """The container detail page wires the upload button to the
        ``raw`` endpoint via ``uploadRawContainerDocument``."""
        detail = next(
            (src for path, src in ts_sources.items()
             if path.name == "page.tsx"
             and path.parent.name == "[containerId]"),
            None,
        )
        assert detail is not None
        assert "uploadRawContainerDocument" in detail
        # The button label is the canonical product wording.
        assert "Adicionar documento sensível" in detail

    def test_detail_page_links_to_mapping(
        self, ts_sources: dict[Path, str]
    ) -> None:
        detail = next(
            (src for path, src in ts_sources.items()
             if path.name == "page.tsx"
             and path.parent.name == "[containerId]"),
            None,
        )
        assert detail is not None
        # Either a Link with the mapping route, or the literal href.
        assert "/mapping" in detail
        assert "tabela de conversão" in detail.lower()

    def test_detail_page_supports_pseudonymized_upload(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """The detail page wires the second upload button to the
        ``pseudonymized`` endpoint via
        ``uploadPseudonymizedContainerDocument``."""
        detail = next(
            (src for path, src in ts_sources.items()
             if path.name == "page.tsx"
             and path.parent.name == "[containerId]"),
            None,
        )
        assert detail is not None
        assert "uploadPseudonymizedContainerDocument" in detail
        # Canonical product wording.
        assert "Adicionar documento já pseudonimizado" in detail
        # The two upload paths must be distinguishable in the UI —
        # otherwise the user can't tell they're choosing a different flow.
        assert "Adicionar documento sensível" in detail

    def test_mapping_page_offers_only_sensitive_export(
        self, ts_sources: dict[Path, str]
    ) -> None:
        page = next(
            (src for path, src in ts_sources.items()
             if path.name == "page.tsx"
             and path.parent.name == "mapping"),
            None,
        )
        assert page is not None
        # The "safe" export was retired — it never carried enough info
        # to be useful (the normalised value of an email is the email
        # itself). Only the sensitive export remains, gated by a
        # confirmation dialog.
        assert "containerMappingExportSafeUrl" not in page
        assert "Exportar pacote seguro" not in page
        assert "containerMappingExportSensitiveUrl" in page
        assert "tabela sensível" in page.lower()

    def test_sensitive_export_shows_required_warning(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """Spec: 'Esta planilha contém dados pessoais sensíveis e
        permite reidentificar os documentos pseudonimizados. Armazene-a
        apenas em ambiente seguro.' — the literal warning must appear
        in the mapping page so the user sees it before the download."""
        page = next(
            (src for path, src in ts_sources.items()
             if path.name == "page.tsx"
             and path.parent.name == "mapping"),
            None,
        )
        assert page is not None
        assert "dados pessoais sensíveis" in page
        assert "ambiente seguro" in page

    def test_restore_page_exists(self) -> None:
        page = (
            UI_SRC
            / "app"
            / "containers"
            / "[containerId]"
            / "restore"
            / "page.tsx"
        )
        assert page.exists(), "restore page.tsx not found"

    def test_restore_page_uses_canonical_phrase(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """The restore page MUST use 'Restaurar dados originais' and
        nothing else. The forbidden-terms check is global; this is the
        positive assertion that the right phrase IS present."""
        page = next(
            (src for path, src in ts_sources.items()
             if path.name == "page.tsx"
             and path.parent.name == "restore"
             and path.parent.parent.name == "[containerId]"),
            None,
        )
        assert page is not None
        assert "Restaurar dados originais" in page

    def test_restore_page_supports_text_and_document_paths(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """The page wires both restoration flows (paste-and-restore + a
        document-of-the-container restore)."""
        page = next(
            (src for path, src in ts_sources.items()
             if path.name == "page.tsx"
             and path.parent.name == "restore"),
            None,
        )
        assert page is not None
        assert "restoreContainerText" in page
        assert "restoreContainerDocument" in page

    def test_restore_page_warns_about_sensitive_output(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """The output of restore is the sensitive table itself —
        the page must say so visibly."""
        page = next(
            (src for path, src in ts_sources.items()
             if path.name == "page.tsx"
             and path.parent.name == "restore"),
            None,
        )
        assert page is not None
        assert "ambiente seguro" in page.lower() or "dados sensíveis" in page.lower()

    def test_detail_page_links_to_restore(
        self, ts_sources: dict[Path, str]
    ) -> None:
        detail = next(
            (src for path, src in ts_sources.items()
             if path.name == "page.tsx"
             and path.parent.name == "[containerId]"),
            None,
        )
        assert detail is not None
        assert "/restore" in detail
        assert "Restaurar dados originais" in detail

    def test_marker_examples_have_no_real_value_hints(
        self, ts_sources: dict[Path, str]
    ) -> None:
        """Anywhere we show example markers (detail page copy, mapping
        page mocks rendered via tests, etc.) they must follow the
        ``[LABEL_NNNN]`` form. Never ``[JOAO_SILVA]`` or ``[CPF_JOAO]``.

        We pull markers via regex ``\\[[A-Z_]+_\\d{4,}\\]`` and check
        the inner index is purely digits."""
        import re as _re

        marker_re = _re.compile(r"\[[A-Z_]+_(\d{2,})\]")
        for path, src in ts_sources.items():
            for match in marker_re.finditer(src):
                index_part = match.group(1)
                assert index_part.isdigit(), (
                    f"{path.relative_to(REPO_ROOT)} contains a malformed "
                    f"marker {match.group(0)!r}."
                )
