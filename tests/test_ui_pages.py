"""Rendering and contract tests for OCR and correction batch pages.

The Flask test client checks that controls, polling hooks, progress displays,
and force-processing options are present in the rendered HTML.
"""

import json
import os
import sys

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
        assert "force-ocr" in content
        assert "forceVal" in content

    def test_page_resumes_polling_on_load(self, client):
        """Check that OCR resumes polling an active batch."""
        # Vérifier que le JS de reprise existe
        resp = client.get("/ocr")
        content = resp.data.decode("utf-8")
        assert "/api/batch/status" in content
        assert "data.running" in content


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
