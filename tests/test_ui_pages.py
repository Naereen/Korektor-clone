"""Rendering and contract tests for OCR and correction batch pages.

The Flask test client checks that controls, polling hooks, progress displays,
and force-processing options are present in the rendered HTML.
"""

import json
import os
import sys
from html import escape

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "correct_ai"))


@pytest.fixture
def client():
    """Return a Flask test client for UI rendering tests."""
    from app import app

    return app.test_client()


class TestOcrPage:
    def test_page_loads(self, client):
        """Ensure the OCR page renders successfully."""
        resp = client.get("/ocr")
        assert resp.status_code == 200

    def test_page_has_elements(self, client):
        """Check that OCR controls and progress elements are present."""
        resp = client.get("/ocr")
        content = resp.data.decode("utf-8")
        assert "Lancer l'extraction OCR" in content
        assert "Annuler" in content
        assert "log-console" in content
        assert "progress-bar" in content
        assert "data.elapsed_seconds" in content
        assert "data.eta_seconds" in content
        assert "force-ocr" in content
        assert "forceVal" in content

    def test_page_resumes_polling_on_load(self, client):
        """Check that OCR resumes polling an active batch."""
        # Vérifier que le JS de reprise existe
        resp = client.get("/ocr")
        content = resp.data.decode("utf-8")
        assert "/api/batch/status" in content
        assert "data.running" in content

    def test_successful_batch_advances_to_ocr_verification(self, client):
        """Ensure only a completed OCR batch advances to verification."""
        content = client.get("/ocr").data.decode("utf-8")

        assert "data.action === 'Terminé'" in content
        assert "window.location.href = '/verify_ocr';" in content


class TestVerifyOcrPage:
    def test_markdown_editor_uses_codemirror(self, client):
        """Ensure OCR text editing uses CodeMirror in Markdown mode."""
        response = client.get("/verify_ocr")
        content = response.data.decode("utf-8")

        assert response.status_code == 200
        assert "codemirror/5.65.16/codemirror.min.css" in content
        assert "codemirror/5.65.16/codemirror.min.js" in content
        assert "CodeMirror.fromTextArea(markdownEditor" in content
        assert "mode: 'markdown'" in content

    def test_finish_action_advances_to_correction(self, client):
        """Ensure OCR review has an explicit save-aware next action."""
        content = client.get("/verify_ocr").data.decode("utf-8")

        assert 'id="finish-btn"' in content
        assert "window.location.href = '/correction';" in content


class TestCorrectionPage:
    def test_page_loads(self, client):
        """Ensure the correction page renders successfully."""
        resp = client.get("/correction")
        assert resp.status_code == 200

    def test_page_has_elements(self, client):
        """Check that correction controls and force options are present."""
        resp = client.get("/correction")
        content = resp.data.decode("utf-8")
        assert "Lancer la correction" in content
        assert "Annuler" in content
        assert "log-console" in content
        assert "progress-bar" in content
        assert "data.elapsed_seconds" in content
        assert "data.eta_seconds" in content
        assert "force-correct" in content

    def test_page_validates_force_submission(self, client):
        """Check that correction force state is sent to the batch API."""
        resp = client.get("/correction")
        content = resp.data.decode("utf-8")
        assert "forceVal" in content
        assert "force: forceVal" in content

    def test_page_resumes_polling_on_load(self, client):
        """Check that correction resumes polling an active batch."""
        resp = client.get("/correction")
        content = resp.data.decode("utf-8")
        assert "/api/batch/status" in content
        assert "data.running" in content

    def test_successful_batch_advances_to_validation(self, client):
        """Ensure only a completed correction batch advances to validation."""
        content = client.get("/correction").data.decode("utf-8")

        assert "data.action === 'Terminé'" in content
        assert "window.location.href = '/verify';" in content


@pytest.mark.parametrize("page", ["/ocr", "/correction"])
def test_console_copy_control(client, page):
    """Both batch pages expose a working clipboard action for their logs."""
    content = client.get(page).data.decode("utf-8")

    assert 'id="btn-copy-console"' in content
    assert 'onclick="copyConsoleOutput()"' in content
    assert "document.getElementById('log-console').innerText" in content
    assert "navigator.clipboard.writeText(text)" in content
    assert "document.execCommand('copy')" in content


class TestVerifyCorrectionPage:
    def test_finish_action_advances_to_bilan(self, client):
        """Ensure correction review saves before advancing to the dashboard."""
        response = client.get("/verify")
        content = response.data.decode("utf-8")

        assert response.status_code == 200
        assert 'id="finish-btn"' in content
        assert "window.location.href = '/bilan';" in content

    def test_crop_preview_and_student_pdf_action(self, client):
        """Correction review exposes an accessible crop preview and PDF action."""
        content = client.get("/verify").data.decode("utf-8")
        assert 'id="student-pdf-btn"' in content
        assert 'id="crop-preview"' in content
        assert 'id="crop-zones"' in content
        assert "/api/corrections/${row.id}/crop" in content
        assert "/api/copies/${currentCopy}/pdf" in content

    def test_question_selection_navigates_pdf_to_annotated_page(self, client):
        """Selecting a question navigates the PDF viewer to its bounding-box page."""
        content = client.get("/verify").data.decode("utf-8")

        assert "function scrollPdfToQuestion(row)" in content
        assert "row?.bounding_boxes" in content
        assert "page=${page}" in content
        assert "scrollPdfToQuestion(row);" in content


class TestDocumentationPage:
    """Verify the in-application documentation route and navigation entry."""

    def test_page_loads(self, client):
        """Ensure the documentation page renders successfully."""
        response = client.get("/documentation")
        assert response.status_code == 200

    def test_page_contains_readme_sections(self, client):
        """Ensure the page exposes the main README documentation sections."""
        response = client.get("/documentation")
        content = response.data.decode("utf-8")
        assert "Fonctionnalités" in content
        assert "Installation" in content
        assert "Structure des données" in content
        assert "Commandes CLI" in content

    def test_page_covers_recent_readme_features(self, client):
        """The in-app guide includes the rubric, crop data, and Typst booklet."""
        content = client.get("/documentation").data.decode("utf-8")
        assert "Vérif Barème" in content
        assert "Import vers Vérif Barème" in content
        assert "Double-cliquez" in content
        assert "bounding_boxes" in content
        assert "crop_image_path" in content
        assert "Livret PDF individuel (Typst)" in content
        assert "correct_ai.pdf_builder" in content
        assert "generate_student_pdf" in content
        assert "rapports_pdf/Copie_01.pdf" in content

    def test_navigation_contains_documentation_after_bilan(self, client):
        """Ensure Documentation appears after Bilan in the shared navigation."""
        content = client.get("/documentation").data.decode("utf-8")
        assert content.index("Bilan") < content.index("Documentation")

    def test_navigation_displays_current_project_directory(self, client):
        """Ensure the navbar shows the project and exposes its full path."""
        from app import config

        content = client.get("/documentation").data.decode("utf-8")
        assert '<a href="/settings">Projet :</a>' in content
        assert 'class="project-dir-path" id="currentProjectName"' in content
        assert f'title="{escape(config.project_dir, quote=True)}"' in content

    def test_footer_contains_project_and_repository_information(self, client):
        """Ensure the shared footer exposes project, navigation, and version data."""
        content = client.get("/documentation").data.decode("utf-8")
        assert "site-footer" in content
        assert "Projet actif" in content
        assert "Philippe HELLUY" in content
        assert "Commit" in content
        assert "/documentation" in content
