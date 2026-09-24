"""Tests for the Bilan page and validation-related SQLite endpoints.

These tests verify dashboard rendering, statistics response shape, student
metadata returned to the validator, and teacher score persistence.
"""

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "correct_ai"))


@pytest.fixture
def client():
    """Return a Flask test client for the application."""
    from app import app, config

    config.project_dir = os.path.join(PROJECT_ROOT, "exemple")
    return app.test_client()


class TestBilanPage:
    def test_page_loads(self, client):
        """Ensure the dashboard route renders successfully."""
        resp = client.get("/bilan")
        assert resp.status_code == 200

    def test_page_has_expected_elements(self, client):
        """Check that the dashboard exposes its primary sections."""
        resp = client.get("/bilan")
        content = resp.data.decode("utf-8")
        assert "Copies" in content
        assert "Entièrement corrigées" in content
        assert "Copies problématiques" in content
        assert "Note / 20" in content
        assert "scoreOutOfTwenty" in content

    def test_question_dispersion_reserves_space_for_title(self, client):
        """Ensure the Plotly title is not clipped by the chart boundary."""
        content = client.get("/bilan").data.decode("utf-8")

        assert "margin: { t: 70, r: 10, b: 70, l: 45 }" in content
        assert "y: 0.97" in content


class TestBilanApi:
    def test_stats_returns_data(self, client):
        """Verify the statistics endpoint returns its documented fields."""
        resp = client.get("/api/bilan/stats")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "nb_copies" in data
        assert "nb_questions" in data
        assert "nb_corrections" in data
        assert "nb_fully_corrected" in data
        assert "max_points" in data
        assert "notes" in data
        assert "problematic" in data

    def test_stats_consistency(self, client):
        """Verify returned copy and correction counts remain consistent."""
        resp = client.get("/api/bilan/stats")
        data = json.loads(resp.data)
        # Les notes doivent correspondre aux copies
        assert len(data["notes"]) <= data["nb_copies"]
        # Le total des corrections par copie ne peut pas dépasser nb_questions
        for note in data["notes"]:
            assert note["nb_corr"] <= data["nb_questions"]


class TestValidationApi:
    @pytest.fixture
    def database_client(self, client, tmp_path, monkeypatch):
        """Build a temporary SQLite project and return its Flask client."""
        from app import config
        from database import get_db, init_db

        monkeypatch.setattr(config, "project_dir", str(tmp_path))
        db = get_db(str(tmp_path))
        init_db(db)
        db.execute(
            "INSERT INTO bareme "
            "(question, texte_question, type_qcm, reponse_attendue, points) "
            "VALUES (?, ?, ?, ?, ?)",
            ["Q1", "Question", 0, "Attendu", 2.0],
        )
        db.execute(
            "INSERT INTO copies (id, etudiant_id, nom, prenom) VALUES (?, ?, ?, ?)",
            [1, "42", "DUPONT", "Alice"],
        )
        db.execute(
            "INSERT INTO corrections "
            "(copie_id, bareme_id, reponse_etudiant, note_llm, commentaire_llm) "
            "VALUES (?, ?, ?, ?, ?)",
            [1, 1, "Réponse", 1.0, "LLM"],
        )
        db.commit()
        db.close()
        return client

    def test_copies_include_student_metadata(self, database_client):
        """Ensure validator copy listings include student identity fields."""
        response = database_client.get("/api/db/copies")

        assert response.status_code == 200
        assert response.get_json() == [
            {"id": 1, "etudiant_id": "42", "nom": "DUPONT", "prenom": "Alice"}
        ]

    def test_professor_note_becomes_final_note(self, database_client):
        """Ensure a saved teacher score overrides the LLM score."""
        response = database_client.post(
            "/api/db/corrections/1",
            json=[
                {
                    "question": "Q1",
                    "note": "1.75",
                    "commentaires eventuels du correcteur": "Prof",
                    "reponses etudiant": "Réponse corrigée",
                }
            ],
        )

        assert response.status_code == 200
        correction = database_client.get("/api/db/corrections/1").get_json()[0]
        assert correction["note"] == 1.75
        assert correction["validee_par_prof"] == 1
