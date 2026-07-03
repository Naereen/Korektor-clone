"""
Tests pour la page Bilan et l'API /api/bilan/stats.
"""
import pytest
import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "correct_ai"))


@pytest.fixture
def client():
    from app import app
    return app.test_client()


class TestBilanPage:
    def test_page_loads(self, client):
        resp = client.get('/bilan')
        assert resp.status_code == 200

    def test_page_has_expected_elements(self, client):
        resp = client.get('/bilan')
        content = resp.data.decode('utf-8')
        assert 'Copies' in content
        assert 'Entièrement corrigées' in content
        assert 'Copies problématiques' in content


class TestBilanApi:
    def test_stats_returns_data(self, client):
        resp = client.get('/api/bilan/stats')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'nb_copies' in data
        assert 'nb_questions' in data
        assert 'nb_corrections' in data
        assert 'nb_fully_corrected' in data
        assert 'max_points' in data
        assert 'notes' in data
        assert 'problematic' in data

    def test_stats_consistency(self, client):
        resp = client.get('/api/bilan/stats')
        data = json.loads(resp.data)
        # Les notes doivent correspondre aux copies
        assert len(data['notes']) <= data['nb_copies']
        # Le total des corrections par copie ne peut pas dépasser nb_questions
        for note in data['notes']:
            assert note['nb_corr'] <= data['nb_questions']
