"""Integration tests for the student Typst PDF generated from SQLite."""

import json
from pathlib import Path

import pymupdf
import pytest
import typst
from PIL import Image

from correct_ai.database import get_db, init_db
from correct_ai.pdf_builder import generate_student_pdf


def test_generate_student_pdf_with_unicode_and_crop(tmp_path, monkeypatch):
    """Teacher priority, class average, Unicode, and cropped images reach the PDF."""
    db = get_db(str(tmp_path))
    init_db(db)
    db.execute("INSERT INTO sujet (contenu) VALUES (?)", ("# Évaluation ∑\nÉnoncé Q1 (--Q1)",))
    db.execute(
        "INSERT INTO corrige (contenu) VALUES (?)",
        (
            "**Q1.** Démontrer ∀ x ∈ ℝ.\n\n"
            "> **Explication :** Argument décisif.\n\n---\n",
        ),
    )
    db.execute(
        "INSERT INTO bareme (id, question, type_qcm, texte_question, points) VALUES (?, ?, ?, ?, ?)",
        (1, "Q1", 0, "**Choisir** :\n\n- une option\n\n```cpp\nint x = 1;\n```", 2),
    )
    db.execute(
        "INSERT INTO bareme (id, question, type_qcm, reponse_attendue, points) "
        "VALUES (2, 'Q2', 1, 'B', 1)"
    )
    db.execute("INSERT INTO copies (id, nom, prenom) VALUES (1, 'Éloi', 'Anaïs')")
    db.execute("INSERT INTO copies (id, nom, prenom) VALUES (2, 'Autre', 'Étudiant')")
    image_dir = tmp_path / "cropped_questions"
    image_dir.mkdir()
    Image.new("RGB", (160, 80), "white").save(image_dir / "q1.png")
    db.execute("""INSERT INTO corrections
           (copie_id, bareme_id, reponse_etudiant, note_llm, commentaire_llm,
            validee_par_prof, note_prof, commentaire_prof, crop_image_path)
           VALUES (1, 1, 'x', 0, 'IA', 1, 1.5, 'Bonne idée : ∀ x', 'cropped_questions/q1.png')""")
    db.execute(
        "INSERT INTO corrections (copie_id, bareme_id, reponse_etudiant, note_llm) "
        "VALUES (1, 2, 'B', 0.5)"
    )
    db.execute(
        "INSERT INTO corrections (copie_id, bareme_id, reponse_etudiant, note_llm) "
        "VALUES (2, 1, 'x', 1)"
    )
    db.commit()
    db.close()

    captured = {}
    real_compile = typst.compile

    def capture_compile(input_path, **kwargs):
        data_path = Path(input_path).parent / "data.json"
        captured.update(json.loads(data_path.read_text(encoding="utf-8")))
        return real_compile(input_path, **kwargs)

    monkeypatch.setattr("correct_ai.pdf_builder.typst.compile", capture_compile)
    result = generate_student_pdf(1, str(tmp_path / "korektor.db"), "rapports_pdf/Copie_01.pdf")

    assert result == str(tmp_path / "rapports_pdf" / "Copie_01.pdf")
    assert captured["nom"] == "Éloi" and captured["prenom"] == "Anaïs"
    assert captured["note_globale"] == "2 / 3"
    assert captured["moyenne_promotion"] == "1.5 / 3"
    assert captured["note_sur_20"] == "13.33 / 20"
    assert captured["moyenne_sur_20"] == "10.00 / 20"
    assert "Démontrer" in captured["questions"][0]["corrige"]
    assert captured["questions"][0]["appreciation"] == "Bonne idée : ∀ x"
    assert captured["questions"][0]["crop_image_path"] == "question-1.png"
    assert captured["questions"][1]["crop_image_path"] == ""
    with pymupdf.open(result) as document:
        text = "\n".join(page.get_text() for page in document)
        assert "Éloi" in text and "Anaïs" in text
        assert "Bonne idée" in text
        assert "Choisir" in text and "une option" in text and "int x = 1;" in text
        assert "Explication :" in text and "Argument décisif." in text
        assert "∀" in text
        assert any(page.get_images() for page in document)
        assert any(
            "pagella" in font[3].lower() or "pagella" in font[4].lower()
            for page in document
            for font in page.get_fonts()
        )


def test_student_report_template_has_fallback_without_data():
    """The template alone must render an explicit placeholder report."""
    template = Path("correct_ai/templates/student_report.typ").resolve()

    compiled = typst.compile(str(template), root=str(template.parent))

    with pymupdf.open(stream=compiled, filetype="pdf") as document:
        text = "\n".join(page.get_text() for page in document)
    assert "Aucune donnée de rapport fournie" in text
    assert "Non renseigné" in text


def test_generate_student_pdf_rejects_unknown_copy(tmp_path):
    """A missing copy must not leave an empty report behind."""
    db = get_db(str(tmp_path))
    init_db(db)
    db.close()
    with pytest.raises(FileNotFoundError, match="Copie 42"):
        generate_student_pdf(42, str(tmp_path / "korektor.db"), "rapports_pdf/Copie_42.pdf")
    assert not (tmp_path / "rapports_pdf" / "Copie_42.pdf").exists()
