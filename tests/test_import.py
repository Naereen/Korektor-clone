"""Tests for project setup, import-page rendering, and rubric generation.

The Flask client is used to verify JSON API contracts for project discovery,
directory selection, and generated rubric output without starting a server.
"""

import json
import io
import os
import sys
from unittest.mock import patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "correct_ai"))


@pytest.fixture
def client():
    """Return a Flask test client for import routes."""
    from app import app

    return app.test_client()


@pytest.fixture
def original_dir():
    """Preserve and restore the application's configured project directory."""
    from app import config

    old = config.project_dir
    yield old
    config.project_dir = old


class TestImportPage:
    def test_page_loads(self, client):
        """Ensure the import page renders successfully."""
        resp = client.get("/import")
        assert resp.status_code == 200
        assert resp.data

    def test_page_has_sections(self, client):
        """Check that the import page exposes its main sections."""
        resp = client.get("/import")
        content = resp.data.decode("utf-8")
        assert "Dossier du projet" in content
        assert "Barème" in content
        assert "Import du PDF" in content

    def test_project_selection_updates_navbar_path(self, client):
        """Ensure selecting a project refreshes the navbar path in place."""
        content = client.get("/import").data.decode("utf-8")
        assert 'id="currentProjectDir"' in content
        assert "currentProjectDir.textContent = data.project_dir" in content

    def test_page_defaults_to_preserving_a4_pdf(self, client):
        """Ensure A4 imports are preserved by default in the UI."""
        content = client.get("/import").data.decode("utf-8")
        assert 'id="alreadyA4"' in content
        assert 'id="alreadyA4" checked' in content


class TestPdfImport:
    def test_a4_pdf_is_copied_without_a3_split(self, client, original_dir, tmp_path):
        """Verify an A4 upload bypasses the A3 splitting pipeline."""
        from app import config

        config.project_dir = str(tmp_path)
        with patch("app.process_a3_split") as split:
            resp = client.post(
                "/api/import",
                data={
                    "file": (io.BytesIO(b"PDF already in A4"), "copies.pdf"),
                    "already_a4": "true",
                    "sort_booklet": "true",
                },
                content_type="multipart/form-data",
            )

        assert resp.status_code == 200
        assert (tmp_path / "copiesA4_sorted.pdf").read_bytes() == b"PDF already in A4"
        split.assert_not_called()

    def test_a3_pdf_still_uses_splitter(self, client, original_dir, tmp_path):
        """Verify disabling A4 mode preserves the existing A3 workflow."""
        from app import config

        config.project_dir = str(tmp_path)
        with patch("app.process_a3_split") as split:
            resp = client.post(
                "/api/import",
                data={
                    "file": (io.BytesIO(b"PDF in A3"), "copies.pdf"),
                    "already_a4": "false",
                    "sort_booklet": "true",
                },
                content_type="multipart/form-data",
            )

        assert resp.status_code == 200
        split.assert_called_once_with(
            str(tmp_path / "temp_import.pdf"),
            str(tmp_path / "copiesA4_sorted.pdf"),
            True,
        )


class TestProjectCheck:
    def test_check_returns_structure(self, client, original_dir):
        """Verify project inspection returns files and copy counters."""
        from app import config

        config.project_dir = os.path.join(PROJECT_ROOT, "exemple")
        config.save()

        resp = client.get("/api/project/check")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "files" in data
        assert "nb_copies_pdf" in data
        assert "nb_extractions" in data
        assert data["files"]["sujet.md"] is True
        assert data["files"]["corrige.md"] is True

    def test_check_missing_files(self, client, original_dir):
        """Verify project inspection reports absent required files."""
        import tempfile

        from app import config

        with tempfile.TemporaryDirectory() as td:
            config.project_dir = td
            config.save()
            resp = client.get("/api/project/check")
            assert resp.status_code == 200
            data = json.loads(resp.data)
            assert data["files"]["sujet.md"] is False


class TestProjectSet:
    def test_set_valid_dir(self, client, original_dir):
        """Verify a valid project directory can be selected."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            resp = client.post("/api/project/set", json={"project_dir": td})
            assert resp.status_code == 200
            data = json.loads(resp.data)
            assert data["success"] is True

    def test_set_invalid_dir(self, client, original_dir):
        """Verify invalid project directories are rejected."""
        resp = client.post("/api/project/set", json={"project_dir": "/nonexistent/path/xyz"})
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["success"] is False


class TestBaremeGenerate:
    def test_generate_bareme(self, client, original_dir):
        """Verify rubric generation returns the expected CSV fields."""
        from app import config

        config.project_dir = os.path.join(PROJECT_ROOT, "exemple")
        config.save()

        resp = client.post("/api/bareme/generate")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["success"] is True
        assert len(data["bareme"]) > 0
        # Vérifier les colonnes attendues
        row = data["bareme"][0]
        assert "question" in row
        assert "réponse attendue" in row
        assert "points" in row

    def test_generate_bareme_no_subject(self, client, original_dir):
        """Verify rubric generation reports a missing subject as an error."""
        import tempfile

        from app import config

        with tempfile.TemporaryDirectory() as td:
            config.project_dir = td
            config.save()
            resp = client.post("/api/bareme/generate")
            assert resp.status_code == 500
            data = json.loads(resp.data)
            assert data["success"] is False

    def test_page_checks_existing_bareme_before_generation(self, client):
        """Verify the UI checks for an existing rubric before generating one."""
        content = client.get("/import").data.decode("utf-8")
        assert "bareme.csv existe déjà" in content
        assert "window.confirm" in content
