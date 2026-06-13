"""
Fixtures pytest pour les tests Korektor.
Utilise uv run pytest pour exécuter.
"""
import pytest
import sqlite3
import os
import shutil
import tempfile
from pathlib import Path


# Chemin du répertoire du projet
PROJECT_DIR = Path(__file__).parent.parent
EXEMPLE2_DIR = PROJECT_DIR / "exemple2"


@pytest.fixture
def db_conn():
    """Base SQLite in-memory pour les tests unitaires."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def temp_test_dir(tmp_path):
    """
    Dossier temporaire pour simuler un projet de test.
    Contient une copie des fichiers nécessaires depuis exemple2/.
    """
    # Copier les fichiers de base depuis exemple2/
    files_to_copy = ["sujet.md", "corrige.md", "bareme.csv", "liste-etudiants.csv"]
    
    for file_name in files_to_copy:
        src = EXEMPLE2_DIR / file_name
        if src.exists():
            shutil.copy(src, tmp_path / file_name)
    
    # Créer un sous-dossier pour les copies (vide par défaut)
    copies_dir = tmp_path / "copies_anonymes_validees"
    copies_dir.mkdir(exist_ok=True)
    
    return str(tmp_path)


@pytest.fixture
def test_db_path(temp_test_dir):
    """Chemin vers une base de test SQLite."""
    return os.path.join(temp_test_dir, "korektor.db")


@pytest.fixture
def mock_llm_response():
    """Réponse LLM mockée pour les tests."""
    return {
        "Q1.1": {
            "note": 1.0,
            "commentaire": "Bonne réponse",
            "reponse_etudiant": "D"
        },
        "Q1.2": {
            "note": 0.5,
            "commentaire": "Réponse partielle",
            "reponse_etudiant": "A,B"
        }
    }


@pytest.fixture
def qwen_test_config():
    """Configuration Qwen pour les tests (sans thinking = gratuit)."""
    return {
        "thinking": False,  # Désactivé = gratuit
        "temperature": 0.0,  # Déterministe
        "max_tokens": 200000,
    }
