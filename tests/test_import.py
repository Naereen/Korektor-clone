"""
Tests pour les nouvelles routes de l'onglet Import (UI refactor).
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


@pytest.fixture
def original_dir():
    """Sauvegarde et restaure le project_dir."""
    from app import config
    old = config.project_dir
    yield old
    config.project_dir = old
    config.save()


class TestImportPage:
    def test_page_loads(self, client):
        resp = client.get('/import')
        assert resp.status_code == 200
        assert resp.data

    def test_page_has_sections(self, client):
        resp = client.get('/import')
        content = resp.data.decode('utf-8')
        assert 'Dossier du projet' in content
        assert 'Barème' in content
        assert 'Import du PDF' in content


class TestProjectCheck:
    def test_check_returns_structure(self, client, original_dir):
        from app import config
        config.project_dir = os.path.join(PROJECT_ROOT, 'exemple')
        config.save()

        resp = client.get('/api/project/check')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'files' in data
        assert 'nb_copies_pdf' in data
        assert 'nb_extractions' in data
        assert data['files']['sujet.md'] is True
        assert data['files']['corrige.md'] is True

    def test_check_missing_files(self, client, original_dir):
        import tempfile

        from app import config
        with tempfile.TemporaryDirectory() as td:
            config.project_dir = td
            config.save()
            resp = client.get('/api/project/check')
            assert resp.status_code == 200
            data = json.loads(resp.data)
            assert data['files']['sujet.md'] is False


class TestProjectSet:
    def test_set_valid_dir(self, client, original_dir):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            resp = client.post('/api/project/set',
                               json={'project_dir': td})
            assert resp.status_code == 200
            data = json.loads(resp.data)
            assert data['success'] is True

    def test_set_invalid_dir(self, client, original_dir):
        resp = client.post('/api/project/set',
                           json={'project_dir': '/nonexistent/path/xyz'})
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data['success'] is False


class TestBaremeGenerate:
    def test_generate_bareme(self, client, original_dir):
        from app import config
        config.project_dir = os.path.join(PROJECT_ROOT, 'exemple')
        config.save()

        resp = client.post('/api/bareme/generate')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['success'] is True
        assert len(data['bareme']) > 0
        # Vérifier les colonnes attendues
        row = data['bareme'][0]
        assert 'question' in row
        assert 'réponse attendue' in row
        assert 'points' in row

    def test_generate_bareme_no_subject(self, client, original_dir):
        import tempfile

        from app import config
        with tempfile.TemporaryDirectory() as td:
            config.project_dir = td
            config.save()
            resp = client.post('/api/bareme/generate')
            assert resp.status_code == 500
            data = json.loads(resp.data)
            assert data['success'] is False
