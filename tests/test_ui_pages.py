"""
Tests pour les pages OCR et Correction (UI refactor).
"""

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "correct_ai"))


@pytest.fixture
def client():
    from app import app

    return app.test_client()


class TestOcrPage:
    def test_page_loads(self, client):
        resp = client.get("/ocr")
        assert resp.status_code == 200

    def test_page_has_elements(self, client):
        resp = client.get("/ocr")
        content = resp.data.decode("utf-8")
        assert "Lancer l'extraction OCR" in content
        assert "Annuler" in content
        assert "log-console" in content
        assert "progress-bar" in content

    def test_page_resumes_polling_on_load(self, client):
        # Vérifier que le JS de reprise existe
        resp = client.get("/ocr")
        content = resp.data.decode("utf-8")
        assert "/api/batch/status" in content
        assert "data.running" in content


class TestCorrectionPage:
    def test_page_loads(self, client):
        resp = client.get("/correction")
        assert resp.status_code == 200

    def test_page_has_elements(self, client):
        resp = client.get("/correction")
        content = resp.data.decode("utf-8")
        assert "Lancer la correction" in content
        assert "Annuler" in content
        assert "log-console" in content
        assert "progress-bar" in content
        assert "force-correct" in content

    def test_page_resumes_polling_on_load(self, client):
        resp = client.get("/correction")
        content = resp.data.decode("utf-8")
        assert "/api/batch/status" in content
        assert "data.running" in content
