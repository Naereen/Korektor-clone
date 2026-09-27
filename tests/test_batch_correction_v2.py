"""Unit and integration tests for the incremental correction workflow.

The suite exercises JSON response parsing, mocked correction persistence,
previous-copy context, teacher/LLM score priority, and aggregate statistics.
All fixtures use temporary projects so tests never depend on live providers.
"""

import json
import os
import sqlite3
import sys

import pymupdf
import pytest
from PIL import Image

# Ajouter le répertoire parent au path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "correct_ai"))

from correct_ai.batch_correction_v2 import (
    _ocr_text_for_page,
    _prepare_correction_data,
    _valid_bounding_boxes,
    correct_copy_incremental,
    correct_copy_incremental_mock,
    parse_llm_response,
)
from correct_ai.llm_providers import ProviderConfig
from correct_ai.database import (
    get_bareme,
    get_corrige,
    get_db,
    get_note_for_copy,
    get_previous_corrections,
    get_sujet,
    init_db,
    load_all_extractions,
    load_bareme,
    load_corrige,
    load_sujet,
    save_correction,
)
from correct_ai.prompt_builder import build_incremental_prompt

# =============================================================================
# Fixtures
# =============================================================================

EXEMPLE_DIR = os.path.join(PROJECT_ROOT, "exemple")


@pytest.fixture
def test_db_with_full_data(tmp_path):
    """Base SQLite avec données chargées depuis exemple/ (si disponible)."""
    import shutil

    # Copier exemple/ vers un dossier temporaire (si disponible)
    test_dir = tmp_path / "test_project"
    test_dir.mkdir()

    # Copier les fichiers nécessaires
    for f in ["sujet.md", "corrige.md", "bareme.csv", "liste-etudiants.csv"]:
        src = os.path.join(EXEMPLE_DIR, f)
        if os.path.exists(src):
            shutil.copy(src, test_dir / f)

    # Copier un sous-ensemble de copies pour les tests
    copies_dir = test_dir / "copies_anonymes_validees"
    copies_dir.mkdir()

    # Copier les 3 premières copies
    for i in range(1, 4):
        src_copy_dir = os.path.join(EXEMPLE_DIR, "copies_anonymes_validees", f"Copie_{i:02d}")
        if os.path.exists(src_copy_dir):
            dst_copy_dir = copies_dir / f"Copie_{i:02d}"
            shutil.copytree(src_copy_dir, dst_copy_dir)

        # Copier le PDF aussi
        src_pdf = os.path.join(EXEMPLE_DIR, "copies_anonymes_validees", f"Copie_{i:02d}.pdf")
        if os.path.exists(src_pdf):
            shutil.copy(src_pdf, copies_dir / f"Copie_{i:02d}.pdf")

    # Initialiser la base
    db = get_db(str(test_dir))
    init_db(db)

    # Charger les données
    load_sujet(db, str(test_dir / "sujet.md"))
    load_corrige(db, str(test_dir / "corrige.md"))

    with open(str(test_dir / "sujet.md"), "r", encoding="utf-8") as f:
        sujet_content = f.read()
    load_bareme(db, str(test_dir / "bareme.csv"), sujet_content)

    # Charger les extractions
    load_all_extractions(db, str(test_dir))

    yield db, str(test_dir)

    db.close()


# =============================================================================
# Tests de parse_llm_response (3.1)
# =============================================================================


class TestParseLlmResponse:
    """Tests de la fonction parse_llm_response."""

    def test_parse_valid_json(self):
        """3.1 : Parser une réponse LLM valide en JSON."""
        response = '{"Q0": {"note": 1.0, "commentaire": "Bonne réponse", "reponse_etudiant": "D"}}'
        result = parse_llm_response(response)

        assert isinstance(result, dict), "Résultat devrait être un dict"
        assert "Q0" in result, "Q0 devrait être dans le résultat"
        assert result["Q0"]["note"] == 1.0, "Note incorrecte"
        assert result["Q0"]["commentaire"] == "Bonne réponse", "Commentaire incorrect"

    def test_parse_json_with_single_quotes(self):
        """3.1 : Parser une réponse JSON avec quotes simples."""
        response = "{'Q0': {'note': 1.0, 'commentaire': 'Bonne réponse', 'reponse_etudiant': 'D'}}"
        result = parse_llm_response(response)

        assert isinstance(result, dict), "Résultat devrait être un dict"
        assert "Q0" in result, "Q0 devrait être dans le résultat"

    def test_parse_json_with_extra_text(self):
        """3.1 : Parser une réponse JSON entourée de texte."""
        response = 'Voici ma réponse: {"Q0": {"note": 1.0, "commentaire": "OK"}}'
        result = parse_llm_response(response)

        assert isinstance(result, dict), "Résultat devrait être un dict"
        assert "Q0" in result, "Q0 devrait être dans le résultat"

    def test_parse_qwen_thinking_and_fenced_questions(self):
        """Qwen thinking is discarded and its questions array is normalized."""
        response = """<think>{\"ignored\": true}</think>
```json
{"questions": [{"num_question": 1, "note": 2.5, "bareme": 4.0,
"commentaire": "Erreur", "bbox_2d": [100, 250, 900, 450]}]}
```
Texte final."""

        result = parse_llm_response(response)

        assert list(result) == ["Q1"]
        assert result["Q1"]["bbox_2d"] == [100, 250, 900, 450]
        assert result["Q1"]["reponse_etudiant"] == ""
        assert result["Q1"]["raisonnement"] == ""

    def test_parse_page_scoped_locations(self):
        """The new locations format is preserved for multi-page answers."""
        response = json.dumps(
            {
                "questions": [
                    {
                        "num_question": 2,
                        "locations": [
                            {"page": 1, "bbox": [100, 750, 900, 980]},
                            {"page": 2, "bbox": [100, 50, 900, 300]},
                        ],
                    }
                ]
            }
        )

        result = parse_llm_response(response)

        assert result["Q2"]["locations"][1]["page"] == 2

    def test_parse_uses_first_valid_json_fence(self):
        """An invalid fenced candidate is skipped in favor of the first valid one."""
        response = '```json\nnot json\n```\n```json\n{"Q2": {"note": 1}}\n```'
        assert parse_llm_response(response) == {"Q2": {"note": 1}}

    def test_parse_empty_response(self):
        """3.1 : Parser une réponse vide."""
        response = ""
        result = parse_llm_response(response)

        assert isinstance(result, dict), "Résultat devrait être un dict"
        assert len(result) == 0, "Résultat devrait être vide"

    def test_parse_malformed_json(self):
        """3.1 : Parser une réponse JSON mal formatée."""
        response = '{"Q0": {"note": 1.0, "commentaire": "Bonne réponse"'
        result = parse_llm_response(response)

        # Devrait retourner un dict (peut-être vide ou partiellement parsé)
        assert isinstance(result, dict), "Résultat devrait être un dict"


@pytest.mark.parametrize(
    ("value", "page", "expected"),
    [
        ([100, 100, 500, 500], 2, [{"bbox_2d": [100, 100, 500, 500], "page": 2}]),
        (
            [{"bbox": [100, 100, 500, 500], "page": 1, "label": "réponse"}],
            None,
            [{"bbox_2d": [100, 100, 500, 500], "label": "réponse", "page": 1}],
        ),
        (
            [
                {"bbox_2d": [100, 100, 500, 500], "page": True},
                {"bbox_2d": [100, 100, 500, 500], "page": 0},
                {"bbox_2d": [0, 0, 20, 500], "page": 1},
            ],
            None,
            [],
        ),
    ],
)
def test_valid_bounding_boxes_normalizes_and_filters_model_locations(value, page, expected):
    assert _valid_bounding_boxes(value, page) == expected


def test_multimodal_boxes_are_saved_and_cropped(tmp_path, monkeypatch):
    """Repaired LLM JSON saves boxes and renders only unambiguous page crops."""
    from correct_ai import llm_providers

    pdf_path = tmp_path / "copy.pdf"
    with pymupdf.open() as document:
        document.new_page(width=100, height=100)
        second = document.new_page(width=100, height=100)
        second.draw_rect(pymupdf.Rect(0, 0, 100, 100), color=(0, 0, 1), fill=(0, 0, 1))
        document.save(pdf_path)

    db = get_db(str(tmp_path))
    init_db(db)
    db.execute("INSERT INTO sujet (contenu) VALUES ('Sujet')")
    db.execute("INSERT INTO corrige (contenu) VALUES ('Corrigé')")
    for question in ("Q1", "Q2", "Q3", "Q4"):
        db.execute("INSERT INTO bareme (question, type_qcm, points) VALUES (?, 0, 1)", (question,))
    db.execute(
        "INSERT INTO copies (id, pdf_path, extraction_texte) VALUES (1, 'copy.pdf', 'Texte OCR')"
    )
    db.commit()

    response = {
        "questions": [
            {
                "num_question": number,
                "note": 0 if number == 1 else 1,
                "bareme": 1.0,
                "commentaire": "Erreur" if number == 1 else "OK",
                "bbox_2d": (
                    [100, 250, 900, 450]
                    if number in (1, 4)
                    else ([0, 0, 20, 20] if number == 2 else None)
                ),
            }
            for number in range(1, 5)
        ]
    }
    prompts = []
    inference_events = []

    def fake_ask_llm_with_pdf(**kwargs):
        prompts.append((kwargs["prompt_text"], kwargs["system_prompt"]))
        kwargs["inference_callback"](True)
        kwargs["inference_callback"](False)
        return f"<think>analyse privée</think>\n```json\n{json.dumps(response)}\n```"

    monkeypatch.setattr(llm_providers, "ask_llm_with_pdf", fake_ask_llm_with_pdf)
    try:
        assert correct_copy_incremental(
            1, "ILAAS_OSS", db, str(tmp_path), inference_callback=inference_events.append
        )
        assert inference_events == [True, False]
        assert '"questions": [' in prompts[0][0]
        assert '"locations": []' in prompts[0][0]
        assert "clé locations" in prompts[0][1]
        assert "plusieurs pages" in prompts[0][1]
        rows = {
            row["question"]: row
            for row in db.execute(
                "SELECT b.question, c.bounding_boxes, c.crop_image_path "
                "FROM corrections c JOIN bareme b ON b.id = c.bareme_id"
            )
        }
        assert len(rows) == 4
        assert json.loads(rows["Q1"]["bounding_boxes"]) == [
            {"bbox_2d": [100, 250, 900, 450], "page": 1}
        ]
        assert json.loads(rows["Q2"]["bounding_boxes"]) == [
            {"bbox_2d": None, "fallback": True, "page": 1},
            {"bbox_2d": None, "fallback": True, "page": 2},
        ]
        assert json.loads(rows["Q3"]["bounding_boxes"]) == [
            {"bbox_2d": None, "fallback": True, "page": 2}
        ]
        assert json.loads(rows["Q4"]["bounding_boxes"]) == [
            {"bbox_2d": [100, 250, 900, 450], "page": 2}
        ]
        for row in rows.values():
            crop = tmp_path / row["crop_image_path"]
            assert crop.is_file() and crop.suffix == ".png"
    finally:
        db.close()


def test_unusable_llm_response_does_not_mark_copy_corrected(test_db_with_full_data, monkeypatch):
    """An unusable repaired response must fail rather than silently succeed."""
    from correct_ai import llm_providers

    db, project_dir = test_db_with_full_data
    attempts = []
    monkeypatch.setattr(
        llm_providers,
        "ask_llm_with_pdf",
        lambda **kwargs: attempts.append(kwargs) or '{"Unknown": {"note": 1}}',
    )
    monkeypatch.setattr("correct_ai.batch_correction_v2.time.sleep", lambda _: None)

    assert not correct_copy_incremental(1, "ILAAS_OSS", db, project_dir)
    assert len(attempts) == 2
    assert db.execute("SELECT COUNT(*) FROM corrections WHERE copie_id = 1").fetchone()[0] == 0


def test_missing_pdf_preserves_valid_boxes(tmp_path):
    """Failure to render a page must not discard valid location data."""
    from correct_ai.batch_correction_v2 import _prepare_correction_data

    box = {"bbox_2d": [100, 100, 500, 500], "label": "Erreur"}
    prepared = _prepare_correction_data(
        {"note": 0, "bbox_2d": box["bbox_2d"]},
        1,
        1,
        "missing.pdf",
        str(tmp_path),
        1,
        1,
    )
    assert json.loads(prepared["bounding_boxes"]) == [{"bbox_2d": box["bbox_2d"]}]
    assert prepared["crop_image_path"] is None


def test_explicit_page_and_refined_bbox_are_saved(tmp_path, monkeypatch):
    """A page-scoped high-resolution refinement replaces the initial box."""
    from correct_ai import batch_correction_v2

    pdf_path = tmp_path / "copy.pdf"
    document = pymupdf.open()
    document.new_page(width=200, height=200)
    document.new_page(width=200, height=200)
    document.save(pdf_path)
    document.close()
    calls = []
    inference_events = []

    def fake_refinement(**kwargs):
        calls.append(kwargs)
        kwargs["inference_callback"](True)
        kwargs["inference_callback"](False)
        return '{"bbox_2d": [200, 200, 800, 800]}'

    monkeypatch.setattr(batch_correction_v2, "ask_llm_with_image", fake_refinement)
    extraction = (
        "<!-- ====== page 1 ====== -->\nQ1 first page\n\n"
        "<!-- ====== page 2 ====== -->\nQ2 answer on second page\n"
    )
    prepared = _prepare_correction_data(
        {
            "note": 1,
            "page": 2,
            "bbox_2d": [100, 100, 500, 500],
            "reponse_etudiant": "La réponse vaut 42.",
        },
        7,
        3,
        "copy.pdf",
        str(tmp_path),
        1,
        2,
        provider=ProviderConfig("vision", "http://test", "model"),
        inference_callback=inference_events.append,
        question_label="Q2",
        question_text="Calculer la réponse",
        page_ocr_text=_ocr_text_for_page(extraction, 2),
    )

    boxes = json.loads(prepared["bounding_boxes"])
    assert len(calls) == 1
    assert inference_events == [True, False]
    assert inference_events == [True, False]
    assert "Q2 answer on second page" in calls[0]["prompt_text"]
    assert prepared["crop_image_path"] == "cropped_questions/copie_7_question_3.png"
    assert (tmp_path / prepared["crop_image_path"]).is_file()
    assert boxes[0]["page"] == 2
    assert boxes[0]["bbox_2d"] != [100, 100, 500, 500]


def test_multi_page_locations_are_refined_and_saved_as_composite(tmp_path, monkeypatch):
    """Each consecutive-page location is refined and included in one composite crop."""
    from correct_ai import batch_correction_v2

    pdf_path = tmp_path / "copy.pdf"
    with pymupdf.open() as document:
        document.new_page(width=200, height=200)
        document.new_page(width=200, height=200)
        document.save(pdf_path)
    calls = []

    def fake_refinement(**kwargs):
        calls.append(kwargs)
        return '{"bbox_2d": [200, 200, 800, 800]}'

    monkeypatch.setattr(batch_correction_v2, "ask_llm_with_image", fake_refinement)
    extraction = (
        "<!-- ====== page 1 ====== -->\nRéponse en bas de page 1\n"
        "<!-- ====== page 2 ====== -->\nSuite en haut de page 2\n"
    )
    prepared = _prepare_correction_data(
        {
            "note": 1,
            "locations": [
                {"page": 1, "bbox": [100, 700, 900, 980]},
                {"page": 2, "bbox": [100, 50, 900, 300]},
            ],
            "reponse_etudiant": "Réponse sur deux pages.",
        },
        8,
        4,
        "copy.pdf",
        str(tmp_path),
        2,
        4,
        provider=ProviderConfig("vision", "http://test", "model"),
        page_ocr_text=extraction,
    )

    locations = json.loads(prepared["bounding_boxes"])
    assert [location["page"] for location in locations] == [1, 2]
    assert all(location["bbox_2d"] != [100, 700, 900, 980] for location in locations)
    assert len(calls) == 2
    assert "Réponse en bas de page 1" in calls[0]["prompt_text"]
    assert "Suite en haut de page 2" in calls[1]["prompt_text"]
    crop_path = tmp_path / prepared["crop_image_path"]
    assert crop_path.is_file()
    with Image.open(crop_path) as composite:
        assert composite.format == "PNG"
        assert composite.width > 0 and composite.height > 0


# =============================================================================
# Tests de correct_copy_incremental_mock (3.2)
# =============================================================================


class TestCorrectCopyIncremental:
    """Tests de la fonction correct_copy_incremental avec mock."""

    def test_correct_copy_mock_basic(self, test_db_with_full_data):
        """3.2 : Corriger une copie avec une réponse mockée."""
        db, test_dir = test_db_with_full_data

        # Réponse mockée
        mock_response = {
            "Q0": {
                "note": 1.0,
                "commentaire": "Bonne réponse",
                "reponse_etudiant": "D",
            },
            "Q1": {
                "note": 0.5,
                "commentaire": "Réponse partielle",
                "reponse_etudiant": "A,B",
            },
        }

        # Corriger la copie 1 avec mock
        success = correct_copy_incremental_mock(
            copy_id=1, db=db, mock_response=mock_response, project_dir=test_dir
        )

        assert success, "La correction mock devrait réussir"

        # Vérifier que les corrections sont sauvegardées
        corrections = db.execute("""
            SELECT * FROM corrections WHERE copie_id = 1
        """).fetchall()

        assert len(corrections) > 0, "Aucune correction sauvegardée"

    def test_correct_copy_mock_with_previous_corrections(self, test_db_with_full_data):
        """3.2 : Corriger avec des corrections précédentes."""
        db, test_dir = test_db_with_full_data

        # D'abord, corriger la copie 1
        mock_response_1 = {"Q0": {"note": 1.0, "commentaire": "Bonne", "reponse_etudiant": "D"}}
        correct_copy_incremental_mock(1, db, mock_response_1, test_dir)

        # Ensuite, corriger la copie 2 (devrait inclure les corrections de la copie 1 dans son prompt)
        mock_response_2 = {"Q0": {"note": 1.0, "commentaire": "Bonne", "reponse_etudiant": "D"}}
        success = correct_copy_incremental_mock(2, db, mock_response_2, test_dir)

        assert success, "La correction de la copie 2 devrait réussir"

        # Vérifier que les corrections des 2 copies sont sauvegardées
        corrections_1 = db.execute("SELECT * FROM corrections WHERE copie_id = 1").fetchall()
        corrections_2 = db.execute("SELECT * FROM corrections WHERE copie_id = 2").fetchall()

        assert len(corrections_1) > 0, "Copie 1 devrait avoir des corrections"
        assert len(corrections_2) > 0, "Copie 2 devrait avoir des corrections"

    def test_correct_copy_not_found(self, test_db_with_full_data):
        """3.2 : Tester la gestion d'une copie non trouvée."""
        db, test_dir = test_db_with_full_data

        mock_response = {"Q0": {"note": 1.0, "commentaire": "Test"}}

        # Essayer de corriger une copie qui n'existe pas
        success = correct_copy_incremental_mock(
            copy_id=999, db=db, mock_response=mock_response, project_dir=test_dir
        )

        assert not success, "Devrait échouer pour une copie non trouvée"

    def test_correct_copy_partial_save(self, test_db_with_full_data):
        """3.2 : Tester la sauvegarde partielle (si certaines questions manquent)."""
        db, test_dir = test_db_with_full_data

        # Réponse mockée avec seulement une question
        mock_response = {"Q0": {"note": 1.0, "commentaire": "Bonne", "reponse_etudiant": "D"}}

        success = correct_copy_incremental_mock(1, db, mock_response, test_dir)

        assert success, "La correction devrait réussir même avec une seule question"

        # Vérifier que seule Q0 est sauvegardée
        corrections = db.execute("""
            SELECT b.question FROM corrections c
            JOIN bareme b ON c.bareme_id = b.id
            WHERE c.copie_id = 1
        """).fetchall()

        questions_sauvegardees = [c["question"] for c in corrections]
        assert "Q0" in questions_sauvegardees, "Q0 devrait être sauvegardée"


# =============================================================================
# Tests d'Intégration Database + Correction
# =============================================================================


class TestIntegration:
    """Tests d'intégration entre database et correction."""

    def test_workflow_complete_mock(self, test_db_with_full_data):
        """Tester le workflow complet avec des mocks."""
        db, test_dir = test_db_with_full_data

        # Corriger les copies 1, 2, 3 avec des mocks
        mock_responses = [
            {"Q0": {"note": 1.0, "commentaire": "Bonne", "reponse_etudiant": "D"}},
            {"Q0": {"note": 1.0, "commentaire": "Bonne", "reponse_etudiant": "D"}},
            {"Q0": {"note": 0.5, "commentaire": "Partielle", "reponse_etudiant": "A"}},
        ]

        for i, mock_response in enumerate(mock_responses, start=1):
            success = correct_copy_incremental_mock(i, db, mock_response, test_dir)
            assert success, f"Échec de la correction de la copie {i}"

        # Vérifier que toutes les corrections sont sauvegardées
        for i in range(1, 4):
            corrections = db.execute(
                """
                SELECT * FROM corrections WHERE copie_id = ?
            """,
                [i],
            ).fetchall()
            assert len(corrections) > 0, f"Copie {i} devrait avoir des corrections"

    def test_priority_prof_over_llm(self, test_db_with_full_data):
        """Tester que la correction Prof écrase la correction LLM."""
        db, test_dir = test_db_with_full_data

        # D'abord, sauvegarder une correction LLM
        from correct_ai.database import save_correction

        # Insérer une question dans bareme si nécessaire
        bareme = db.execute("SELECT * FROM bareme LIMIT 1").fetchone()
        if bareme:
            bareme_id = bareme["id"]

            # Sauvegarder une correction LLM
            save_correction(
                db,
                1,
                bareme_id,
                {"reponse_etudiant": "D", "note": 0.5, "commentaire": "LLM: partiel"},
                is_prof=False,
            )

            # Vérifier que la note LLM est sauvegardée
            note_llm = db.execute(
                """
                SELECT note_llm FROM corrections WHERE copie_id = 1 AND bareme_id = ?
            """,
                [bareme_id],
            ).fetchone()
            assert note_llm and note_llm["note_llm"] == 0.5

            # Sauvegarder une correction Prof
            save_correction(
                db,
                1,
                bareme_id,
                {"reponse_etudiant": "D", "note": 1.0, "commentaire": "Prof: bon"},
                is_prof=True,
            )

            # Vérifier que la note finale est celle du prof
            final_note = get_note_for_copy(db, 1, bareme_id)
            assert final_note == 1.0, f"Note finale devrait être 1.0, pas {final_note}"

    def test_get_previous_corrections_order(self, test_db_with_full_data):
        """Tester que get_previous_corrections retourne les bonnes données."""
        db, test_dir = test_db_with_full_data

        # Sauvegarder des corrections pour les copies 1 et 2
        from correct_ai.database import save_correction

        bareme = db.execute("SELECT * FROM bareme LIMIT 1").fetchone()
        if bareme:
            bareme_id = bareme["id"]

            save_correction(
                db,
                1,
                bareme_id,
                {"reponse_etudiant": "D", "note": 1.0, "commentaire": "Bonne"},
                is_prof=False,
            )
            save_correction(
                db,
                2,
                bareme_id,
                {"reponse_etudiant": "D", "note": 1.0, "commentaire": "Bonne"},
                is_prof=False,
            )

            # Récupérer les corrections pour copie_id < 3
            previous = get_previous_corrections(db, 3)

            assert (
                len(previous) == 2
            ), f"Devrait avoir 2 corrections précédentes, pas {len(previous)}"
            assert previous[0]["copie_id"] == 1, "Première correction devrait être copie 1"
            assert previous[1]["copie_id"] == 2, "Deuxième correction devrait être copie 2"


# =============================================================================
# Tests de Statistiques
# =============================================================================


class TestStatistics:
    """Tests des fonctions de statistiques."""

    def test_get_stats_for_copy(self, test_db_with_full_data):
        """Tester get_stats_for_copy."""
        db, test_dir = test_db_with_full_data

        # Sauvegarder quelques corrections
        from correct_ai.database import save_correction

        bareme = db.execute("SELECT * FROM bareme LIMIT 2").fetchall()
        if len(bareme) >= 2:
            for i, q in enumerate(bareme):
                save_correction(
                    db,
                    1,
                    q["id"],
                    {
                        "reponse_etudiant": "Test",
                        "note": (i + 1) * 0.5,
                        "commentaire": "OK",
                    },
                    is_prof=False,
                )

            stats = db.execute("""
                SELECT COUNT(*) as total, AVG(note_llm) as avg_note
                FROM corrections WHERE copie_id = 1
            """).fetchone()

            assert stats["total"] == 2, f"Devrait avoir 2 corrections, pas {stats['total']}"
            assert stats["avg_note"] == 0.75, f"Moyenne devrait être 0.75, pas {stats['avg_note']}"
