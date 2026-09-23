"""
Tests d'Intégration pour Korektor (Phase 4).

Ces tests valident :
- Le workflow complet de correction incrémentale
- La protection des fichiers vérifiés (Copie_XX.pdf et Copie_XX/extraction.md)
- La cohérence des données dans SQLite

Exécution:
    uv run pytest tests/test_integration.py -v
"""
import os
import shutil
import sys

import pytest

# Ajouter le répertoire parent au path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "correct_ai"))

from correct_ai.batch_correction_v2 import (correct_all_incremental,
                                            correct_copy_incremental_mock)
from correct_ai.database import (get_bareme, get_corrige, get_db,
                                 get_previous_corrections, get_sujet, init_db,
                                 load_all_extractions, load_bareme,
                                 load_corrige, load_sujet, save_correction,
                                 save_extraction)

EXEMPLE_DIR = os.path.join(PROJECT_ROOT, "exemple")


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def integration_test_dir(tmp_path):
    """Crée un dossier de test avec une copie partielle de exemple/."""
    test_dir = tmp_path / "integration_test"
    test_dir.mkdir()
    
    # Copier les fichiers de base
    for f in ["sujet.md", "corrige.md", "bareme.csv", "liste-etudiants.csv"]:
        src = os.path.join(EXEMPLE_DIR, f)
        if os.path.exists(src):
            shutil.copy(src, test_dir / f)
    
    # Copier les copies 1-5 pour les tests
    copies_dir = test_dir / "copies_anonymes_validees"
    copies_dir.mkdir()
    
    for i in range(1, 6):
        # Copier le dossier de la copie
        src_copy_dir = os.path.join(EXEMPLE_DIR, "copies_anonymes_validees", f"Copie_{i:02d}")
        if os.path.exists(src_copy_dir):
            dst_copy_dir = copies_dir / f"Copie_{i:02d}"
            shutil.copytree(src_copy_dir, dst_copy_dir)
        
        # Copier le PDF
        src_pdf = os.path.join(EXEMPLE_DIR, "copies_anonymes_validees", f"Copie_{i:02d}.pdf")
        if os.path.exists(src_pdf):
            shutil.copy(src_pdf, copies_dir / f"Copie_{i:02d}.pdf")
    
    return str(test_dir)


@pytest.fixture
def integration_db(integration_test_dir):
    """Base SQLite initialisée avec les données de test."""
    db = get_db(integration_test_dir)
    init_db(db)
    
    # Charger les données
    load_sujet(db, os.path.join(integration_test_dir, "sujet.md"))
    load_corrige(db, os.path.join(integration_test_dir, "corrige.md"))
    
    with open(os.path.join(integration_test_dir, "sujet.md"), "r", encoding="utf-8") as f:
        sujet_content = f.read()
    load_bareme(db, os.path.join(integration_test_dir, "bareme.csv"), sujet_content)
    
    # Charger les extractions
    load_all_extractions(db, integration_test_dir)
    
    yield db
    db.close()


# =============================================================================
# Tests de Protection des Fichiers Vérifiés (CRITIQUE)
# =============================================================================

class TestDataProtection:
    """Tests CRITIQUES : Protection des fichiers vérifiés par un humain."""
    
    def test_verified_extraction_not_overwritten(self, integration_db):
        """
        **TEST CRITIQUE** : Vérifier qu'une extraction marquée comme vérifiée
        n'est PAS écrasée par une nouvelle extraction.
        
        C'est la protection principale demandée : ne jamais perdre les fichiers
        Copie_XX.pdf et Copie_XX/extraction.md vérifiés par un humain.
        """
        # Marquer la copie 1 comme vérifiée manuellement
        integration_db.execute("""
            UPDATE copies SET extraction_verified = 1 WHERE id = 1
        """)
        integration_db.commit()
        
        # Vérifier qu'elle est bien marquée
        result = integration_db.execute("""
            SELECT extraction_verified FROM copies WHERE id = 1
        """).fetchone()
        assert result['extraction_verified'] == 1, "Copie 1 devrait être marquée comme vérifiée"
        
        # Essayer de sauvegarder une nouvelle extraction pour la copie 1
        # ⚠️ Cela devrait ÉCHOUER grâce à la protection
        original_text = integration_db.execute("""
            SELECT extraction_texte FROM copies WHERE id = 1
        """).fetchone()['extraction_texte']
        
        success = save_extraction(
            conn=integration_db,
            copy_id=1,
            pdf_path="nouveau.pdf",
            extraction_text="NOUVEL EXTRACTION - NE DOIT PAS ÊTRE SAUVÉ",
            etudiant_info=None,
            verified=False
        )
        
        # Vérifier que l'écriture a été BLOQUÉE
        assert not success, "❌ PROBLÈME : Une extraction vérifiée a été écrasée !"
        
        # Vérifier que le texte original est toujours là
        current_text = integration_db.execute("""
            SELECT extraction_texte FROM copies WHERE id = 1
        """).fetchone()['extraction_texte']
        assert current_text == original_text, "❌ Le texte d'extraction a été modifié !"
    
    def test_non_verified_extraction_can_be_updated(self, integration_db):
        """
        Vérifier qu'une extraction NON vérifiée peut être mise à jour.
        """
        # Marquer la copie 2 comme NON vérifiée (ou ne pas la marquer)
        integration_db.execute("""
            UPDATE copies SET extraction_verified = 0 WHERE id = 2
        """)
        integration_db.commit()
        
        # Sauvegarder une nouvelle extraction pour la copie 2
        success = save_extraction(
            conn=integration_db,
            copy_id=2,
            pdf_path="nouveau2.pdf",
            extraction_text="Nouvelle extraction pour copie 2",
            etudiant_info=None,
            verified=False
        )
        
        # Vérifier que l'écriture a RÉUSSI
        assert success, "Une extraction non vérifiée devrait pouvoir être mise à jour"
        
        # Vérifier que le nouveau texte est sauvegardé
        current_text = integration_db.execute("""
            SELECT extraction_texte FROM copies WHERE id = 2
        """).fetchone()['extraction_texte']
        assert current_text == "Nouvelle extraction pour copie 2", "Le texte n'a pas été mis à jour"
    
    def test_mark_as_verified_protects_from_future_writes(self, integration_db):
        """
        Vérifier que marquer une extraction comme vérifiée la protège
        des écritures futures.
        """
        # La copie 3 devrait être non vérifiée par défaut
        integration_db.execute("""
            UPDATE copies SET extraction_verified = 0 WHERE id = 3
        """)
        integration_db.commit()
        
        # Sauvegarder une extraction
        save_extraction(
            conn=integration_db,
            copy_id=3,
            pdf_path="copie3_v1.pdf",
            extraction_text="Version 1",
            etudiant_info=None,
            verified=False
        )
        
        # Marquer comme vérifiée
        integration_db.execute("""
            UPDATE copies SET extraction_verified = 1 WHERE id = 3
        """)
        integration_db.commit()
        
        # Essayer d'écrire une nouvelle version
        success = save_extraction(
            conn=integration_db,
            copy_id=3,
            pdf_path="copie3_v2.pdf",
            extraction_text="Version 2 - NE DOIT PAS PASSER",
            etudiant_info=None,
            verified=False
        )
        
        assert not success, "❌ Une extraction vérifiée a été modifiée !"
        
        # Vérifier que Version 1 est toujours là
        current_text = integration_db.execute("""
            SELECT extraction_texte FROM copies WHERE id = 3
        """).fetchone()['extraction_texte']
        assert current_text == "Version 1", "❌ Le texte a été modifié après vérification !"


# =============================================================================
# Tests du Workflow Complet
# =============================================================================

class TestWorkflow:
    """Tests du workflow complet de correction."""
    
    def test_workflow_with_mock(self, integration_test_dir):
        """
        Tester le workflow complet avec des réponses LLM mockées.
        """
        # Corriger les copies 1-3 avec des mocks
        correct_all_incremental(
            start=1,
            end=3,
            project_dir=integration_test_dir,
            provider_name="ILAAS_OSS",
            test_mode=True
        )
        
        # Vérifier que les corrections sont sauvegardées
        db = get_db(integration_test_dir)
        
        for i in range(1, 4):
            corrections = db.execute("""
                SELECT COUNT(*) as count FROM corrections WHERE copie_id = ?
            """, [i]).fetchone()
            assert corrections['count'] > 0, f"Copie {i} devrait avoir des corrections"
        
        db.close()
    
    def test_incremental_context_builds_correctly(self, integration_db):
        """
        Vérifier que le contexte incrémental est bien construit.
        """
        # Corriger la copie 1
        mock_response_1 = {
            "Q0": {"note": 1.0, "commentaire": "Bonne", "reponse_etudiant": "0 (s.size() vaut 0)"}
        }
        project_dir = os.path.dirname(integration_db.execute("PRAGMA database_list").fetchone()['file'])
        correct_copy_incremental_mock(1, integration_db, mock_response_1, project_dir)
        
        # Corriger la copie 2
        mock_response_2 = {
            "Q0": {"note": 1.0, "commentaire": "Bonne", "reponse_etudiant": "0 (s.size() vaut 0)"}
        }
        correct_copy_incremental_mock(2, integration_db, mock_response_2, project_dir)
        
        # Vérifier que get_previous_corrections retourne bien la copie 1 pour la copie 2
        previous = get_previous_corrections(integration_db, 3)
        assert len(previous) >= 2, f"Devrait avoir au moins 2 corrections précédentes, pas {len(previous)}"


# =============================================================================
# Tests de Cohérence des Données
# =============================================================================

class TestDataConsistency:
    """Tests de cohérence des données dans SQLite."""
    
    def test_all_required_tables_exist(self, integration_db):
        """Vérifier que toutes les tables requises existent."""
        tables = integration_db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = [t['name'] for t in tables]
        
        required_tables = ['sujet', 'corrige', 'bareme', 'copies', 'corrections']
        for table in required_tables:
            assert table in table_names, f"Table {table} manquante"
    
    def test_bareme_loaded_correctly(self, integration_db):
        """Vérifier que le barème est chargé correctement."""
        bareme_count = integration_db.execute("SELECT COUNT(*) FROM bareme").fetchone()[0]
        assert bareme_count > 0, "Le barème devrait contenir des questions"
    
    def test_copies_loaded_correctly(self, integration_db):
        """Vérifier que les copies sont chargées correctement."""
        copies_count = integration_db.execute("SELECT COUNT(*) FROM copies").fetchone()[0]
        assert copies_count >= 4, f"Devrait avoir au moins 4 copies, pas {copies_count}"
        
        # Vérifier que les copies ont du texte d'extraction
        for i in range(1, 5):
            copy = integration_db.execute("""
                SELECT extraction_texte FROM copies WHERE id = ?
            """, [i]).fetchone()
            assert copy and copy['extraction_texte'], f"Copie {i} devrait avoir du texte d'extraction"
    
    def test_corrections_saved_correctly(self, integration_db):
        """Vérifier que les corrections sont sauvegardées correctement."""
        # Sauvegarder une correction
        bareme = integration_db.execute("SELECT * FROM bareme LIMIT 1").fetchone()
        if bareme:
            save_correction(
                integration_db, 1, bareme['id'],
                {"reponse_etudiant": "Test", "note": 1.0, "commentaire": "OK"},
                is_prof=False
            )
            integration_db.commit()
            
            # Vérifier
            correction = integration_db.execute("""
                SELECT * FROM corrections WHERE copie_id = 1 AND bareme_id = ?
            """, [bareme['id']]).fetchone()
            assert correction is not None, "Correction non sauvegardée"
            assert correction['note_llm'] == 1.0, "Note incorrecte"
