"""Tests for deterministic incremental and simple prompt construction.

The fixtures model rubrics and prior corrections, while assertions verify
required sections, question coverage, QCM formatting, token-size behavior,
and deterministic output.
"""

import os
import sys

import pytest

# Ajouter le répertoire parent au path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def bareme_data():
    """Données de barème pour les tests."""
    return [
        {
            "id": 1,
            "question": "Q1.1",
            "texte_question": "Quelle est la capital de la France ?",
            "type_qcm": True,
            "reponse_attendue": "Paris",
            "points": 1.0,
        },
        {
            "id": 2,
            "question": "Q1.2",
            "texte_question": "Quel est le plus long fleuve de France ?",
            "type_qcm": False,
            "reponse_attendue": "Loire",
            "points": 2.0,
        },
        {
            "id": 3,
            "question": "Q2.1",
            "texte_question": "Calculer 2+2",
            "type_qcm": False,
            "reponse_attendue": "4",
            "points": 1.5,
        },
    ]


@pytest.fixture
def previous_corrections_data():
    """Données de corrections précédentes pour les tests."""
    return [
        {
            "copie_id": 1,
            "bareme_id": 1,
            "question": "Q1.1",
            "texte_question": "Quelle est la capital de la France ?",
            "reponse_etudiant": "Paris",
            "note": 1.0,
            "commentaire": "Bonne réponse",
        },
        {
            "copie_id": 1,
            "bareme_id": 2,
            "question": "Q1.2",
            "texte_question": "Quel est le plus long fleuve de France ?",
            "reponse_etudiant": "Seine",
            "note": 0.5,
            "commentaire": "Réponse incorrecte",
        },
        {
            "copie_id": 2,
            "bareme_id": 1,
            "question": "Q1.1",
            "texte_question": "Quelle est la capital de la France ?",
            "reponse_etudiant": "Paris",
            "note": 1.0,
            "commentaire": "Bonne réponse",
        },
    ]


# =============================================================================
# Import de la vraie fonction
# =============================================================================

from correct_ai.prompt_builder import (
    build_bbox_refinement_prompt,
    build_incremental_prompt,
    build_simple_prompt,
)

# =============================================================================
# Tests de build_incremental_prompt (2.1)
# =============================================================================


class TestBuildIncrementalPrompt:
    """Tests de la fonction build_incremental_prompt."""

    def test_prompt_without_previous_corrections(self, bareme_data):
        """2.1 : Tester le prompt sans corrections précédentes."""
        sujet = "Sujet de l'examen"
        corrige = "Corrigé de l'examen"
        current_pdf_path = "Copie_03.pdf"
        current_extraction = "Extraction OCR de la copie 3"
        previous_corrections = []

        prompt = build_incremental_prompt(
            sujet=sujet,
            corrige=corrige,
            bareme=bareme_data,
            previous_corrections=previous_corrections,
            current_pdf_path=current_pdf_path,
            current_extraction=current_extraction,
        )

        # Vérifier les sections principales
        assert "Tu es un correcteur expert" in prompt, "Section introduction manquante"
        assert "## SUJET:" in prompt, "Section SUJET manquante"
        assert "Sujet de l'examen" in prompt, "Contenu du sujet manquant"
        assert "## CORRIGÉ OFFICIEL:" in prompt, "Section CORRIGÉ manquante"
        assert "Corrigé de l'examen" in prompt, "Contenu du corrigé manquant"
        assert "## BAREME:" in prompt, "Section BAREME manquante"
        assert "# COPIE COURANTE À CORRIGER" in prompt, "Section COPIE COURANTE manquante"
        assert "Extraction OCR de la copie 3" in prompt, "Extraction manquante"
        assert "les pages de la copie sont fournies en images" in prompt, "PDF manquant"

        # Vérifier que la section CORRECTIONS PRÉCÉDENTES n'est PAS présente
        assert (
            "CORRECTIONS PRÉCÉDENTES" not in prompt
        ), "Section CORRECTIONS PRÉCÉDENTES ne devrait pas être présente"

        # Vérifier les instructions
        assert "INSTRUCTIONS STRICTES" in prompt, "Instructions manquantes"
        assert "TEMPLATE JSON" in prompt, "Template JSON manquant"
        assert '"questions": [' in prompt, "Tableau questions manquant"
        assert prompt.count('"num_question"') == len(bareme_data)
        assert prompt.count('"bbox_2d": null') == len(bareme_data)
        assert prompt.count('"page": null') == len(bareme_data)
        assert prompt.count('"reponse_etudiant": ""') == len(bareme_data)
        assert "Transcris fidèlement dans reponse_etudiant" in prompt
        assert "[xmin, ymin, xmax, ymax]" in prompt
        assert "numéro de page du PDF, à partir de 1" in prompt
        assert "entiers normalisés entre 0 et 1000" in prompt
        assert "entourer l'encre manuscrite réellement visible" in prompt
        assert "dépasser 20 unités" in prompt

    def test_prompt_with_previous_corrections(self, bareme_data, previous_corrections_data):
        """2.2 : Tester le prompt avec corrections précédentes."""
        sujet = "Sujet de l'examen"
        corrige = "Corrigé de l'examen"
        current_pdf_path = "Copie_03.pdf"
        current_extraction = "Extraction OCR de la copie 3"

        prompt = build_incremental_prompt(
            sujet=sujet,
            corrige=corrige,
            bareme=bareme_data,
            previous_corrections=previous_corrections_data,
            current_pdf_path=current_pdf_path,
            current_extraction=current_extraction,
        )

        # Vérifier que la section CORRECTIONS PRÉCÉDENTES est présente
        assert "CORRECTIONS PRÉCÉDENTES" in prompt, "Section CORRECTIONS PRÉCÉDENTES manquante"

        # Vérifier que les corrections des copies 1 et 2 sont présentes
        assert "### Copie 1:" in prompt, "Correction de la copie 1 manquante"
        assert "### Copie 2:" in prompt, "Correction de la copie 2 manquante"

        # Vérifier qu'une correction spécifique est présente
        assert "Note=1.0" in prompt, "Note de la copie 1 manquante"
        assert "Bonne réponse" in prompt, "Commentaire manquant"
        assert "Réponse='Paris'" in prompt, "Réponse étudiante manquante"

    def test_refinement_prompt_uses_ocr_as_context(self):
        """The focused pass scopes coordinates to its crop and uses OCR context."""
        prompt = build_bbox_refinement_prompt(
            "Q2", "Calculer la somme", 3, "La somme vaut 42", "Q2. La somme vaut 42."
        )

        assert "relatives à CE recadrage" in prompt
        assert "La somme vaut 42" in prompt
        assert "Q2. La somme vaut 42." in prompt

    def test_prompt_contains_all_bareme_questions(self, bareme_data):
        """2.1 : Vérifier que toutes les questions du barème sont dans le prompt."""
        sujet = "Sujet"
        corrige = "Corrigé"
        current_pdf_path = "copie.pdf"
        current_extraction = "extraction"
        previous_corrections = []

        prompt = build_incremental_prompt(
            sujet=sujet,
            corrige=corrige,
            bareme=bareme_data,
            previous_corrections=previous_corrections,
            current_pdf_path=current_pdf_path,
            current_extraction=current_extraction,
        )

        # Vérifier que chaque question est présente
        for q in bareme_data:
            assert (
                f"**{q['question']}**" in prompt
            ), f"Question {q['question']} manquante dans le prompt"
            assert (
                q["reponse_attendue"] in prompt
            ), f"Réponse attendue pour {q['question']} manquante"

    def test_prompt_token_count_estimation(self, bareme_data, previous_corrections_data):
        """2.3 : Tester la taille du prompt (estimation tokens)."""
        try:
            import tiktoken

            has_tiktoken = True
        except ImportError:
            has_tiktoken = False
            print("⚠️  tiktoken non disponible - test sauté")

        if not has_tiktoken:
            pytest.skip("tiktoken non installé")

        sujet = "Sujet de l'examen" * 100  # Simuler un long sujet
        corrige = "Corrigé de l'examen" * 100
        current_pdf_path = "Copie_05.pdf"
        current_extraction = "Extraction OCR" * 200

        prompt = build_incremental_prompt(
            sujet=sujet,
            corrige=corrige,
            bareme=bareme_data,
            previous_corrections=previous_corrections_data,
            current_pdf_path=current_pdf_path,
            current_extraction=current_extraction,
        )

        # Compter les tokens
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = len(enc.encode(prompt))

        print(f"\n📊 Estimation tokens: {tokens}")

        # Vérifier que le prompt fait moins de 200K tokens (marge de sécurité)
        assert tokens < 200000, f"Prompt trop grand : {tokens} tokens"


# =============================================================================
# Tests de Format du Prompt
# =============================================================================


class TestPromptFormat:
    """Tests du format du prompt."""

    def test_prompt_has_required_sections(self, bareme_data):
        """Vérifier que le prompt contient toutes les sections requises."""
        prompt = build_incremental_prompt(
            sujet="Sujet",
            corrige="Corrigé",
            bareme=bareme_data,
            previous_corrections=[],
            current_pdf_path="copie.pdf",
            current_extraction="extraction",
        )

        required_sections = [
            "Tu es un correcteur expert",
            "# CONTEXTE GLOBAL",
            "## SUJET:",
            "## CORRIGÉ OFFICIEL:",
            "## BAREME:",
            "# COPIE COURANTE À CORRIGER",
            "## Texte OCR:",
            "## PDF:",
            "# INSTRUCTIONS STRICTES:",
        ]

        for section in required_sections:
            assert section in prompt, f"Section requise manquante : {section}"

    def test_prompt_qcm_type_display(self, bareme_data):
        """Vérifier que le type QCM est bien affiché."""
        prompt = build_incremental_prompt(
            sujet="Sujet",
            corrige="Corrigé",
            bareme=bareme_data,
            previous_corrections=[],
            current_pdf_path="copie.pdf",
            current_extraction="extraction",
        )

        # Q1.1 est un QCM (type_qcm=True)
        assert "QCM" in prompt, "Type QCM non affiché"
        # Q1.2 est Ouverte (type_qcm=False)
        assert "Ouverte" in prompt, "Type Ouverte non affiché"

    def test_prompt_points_display(self, bareme_data):
        """Vérifier que les points sont bien affichés."""
        prompt = build_incremental_prompt(
            sujet="Sujet",
            corrige="Corrigé",
            bareme=bareme_data,
            previous_corrections=[],
            current_pdf_path="copie.pdf",
            current_extraction="extraction",
        )

        # Vérifier que les points sont présents
        for q in bareme_data:
            assert f"{q['points']} pts" in prompt, f"Points pour {q['question']} manquants"


# =============================================================================
# Tests de Uniformité du Prompt
# =============================================================================


class TestPromptConsistency:
    """Tests de cohérence du prompt."""

    def test_prompt_deterministic(self, bareme_data):
        """Vérifier que le prompt est déterministe (même entrée = même sortie)."""
        sujet = "Sujet"
        corrige = "Corrigé"
        current_pdf_path = "copie.pdf"
        current_extraction = "extraction"
        previous_corrections = []

        prompt1 = build_incremental_prompt(
            sujet=sujet,
            corrige=corrige,
            bareme=bareme_data,
            previous_corrections=previous_corrections,
            current_pdf_path=current_pdf_path,
            current_extraction=current_extraction,
        )

        prompt2 = build_incremental_prompt(
            sujet=sujet,
            corrige=corrige,
            bareme=bareme_data,
            previous_corrections=previous_corrections,
            current_pdf_path=current_pdf_path,
            current_extraction=current_extraction,
        )

        assert prompt1 == prompt2, "Prompt non déterministe"

        def test_simple_prompt_contains_copy_and_template(self, bareme_data):
            """The fallback prompt should include OCR, PDF, rubric, and JSON template."""
            prompt = build_simple_prompt(
                sujet="Sujet",
                corrige="Corrigé",
                bareme=bareme_data,
                extraction="Réponse OCR",
                pdf_path="Copie_01.pdf",
            )
            assert "Réponse OCR" in prompt
            assert "les pages de la copie sont fournies en images" in prompt
            assert '"Q1.1"' in prompt

        def test_prompt_token_fallback_without_tiktoken(self, bareme_data, monkeypatch):
            """Token estimation should use its character fallback when tiktoken is absent."""
            import builtins

            original_import = builtins.__import__

            def import_without_tiktoken(name, *args, **kwargs):
                """Reject only the optional tiktoken import."""
                if name == "tiktoken":
                    raise ImportError
                return original_import(name, *args, **kwargs)

            monkeypatch.setattr(builtins, "__import__", import_without_tiktoken)
            assert estimate_prompt_tokens("Sujet", "Corrigé", bareme_data, [], "OCR") > 0
