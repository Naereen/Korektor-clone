"""Integration tests for the student Typst PDF generated from SQLite."""

import json
import re
import shutil
from pathlib import Path

import pymupdf
import pytest
import typst
from PIL import Image

from correct_ai.database import get_db, init_db
from correct_ai.pdf_builder import TEMPLATE_PATH, _image_path, generate_student_pdf


def test_generate_student_pdf_with_unicode_and_crop(tmp_path, monkeypatch):
    """Teacher priority, class average, Unicode, and cropped images reach the PDF."""
    db = get_db(str(tmp_path))
    init_db(db)
    db.execute(
        "INSERT INTO sujet (contenu) VALUES (?)",
        ("# Évaluation ∑\n## Thème **test**\n**Q1.** Énoncé Q1 (--Q1)",),
    )
    db.execute(
        "INSERT INTO corrige (contenu) VALUES (?)",
        (
            "**Q1.** "+r"\underline{\emph{Nom}:} \hfill{}"+
            "\n\n**Choisir** :\n\n- une\n  option\n\n```cpp\nint x = 1;\n```\n\n"
            "**Réponse :**\n\n**Réponse**\n\nDémontrer ∀ x ∈ ℝ.\n\n"
            "Une réponse détaillée reste visible.\n\n"
            "> **Explication :** Argument décisif.\n\n---\n",
        ),
    )
    db.execute(
        "INSERT INTO bareme (id, question, type_qcm, texte_question, points) VALUES (?, ?, ?, ?, ?)",
        (
            1,
            "Q1",
            0,
            "## Thème **test**\n**Q1.** "+r"\underline{\emph{Nom}:} \hfill{}"+
            "\n\n**Choisir** :\n\n- une option\n\n```cpp\nint x = 1;\n```",
            2,
        ),
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
    db.execute(
        """INSERT INTO corrections
           (copie_id, bareme_id, reponse_etudiant, note_llm, commentaire_llm,
            validee_par_prof, note_prof, commentaire_prof, crop_image_path, bounding_boxes)
           VALUES (1, 1, 'x', 0, 'IA', 1, 1.5, 'Bonne idée : "eg" ; chemin C:\\tmp',
                   'cropped_questions/q1.png', ?)""",
        (json.dumps([{"bbox_2d": [100, 100, 500, 500], "label": "Bonne idée"}]),),
    )
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
    assert captured["questions"][0]["section_titles"] == ["Thème #strong[test]"]
    assert "#underline[#emph[Nom]:]" in captured["questions"][0]["enonce"]
    assert "Thème" not in captured["questions"][0]["enonce"]
    assert "Démontrer" in captured["questions"][0]["corrige"]
    assert "Choisir" not in captured["questions"][0]["corrige"]
    assert "int x = 1;" not in captured["questions"][0]["corrige"]
    assert captured["questions"][0]["appreciation"] == 'Bonne idée : "eg" ; chemin C:\\tmp'
    assert captured["questions"][0]["crop_image_path"] == "question-1.png"
    assert captured["questions"][1]["crop_image_path"] == ""
    with pymupdf.open(result) as document:
        page_texts = [page.get_text() for page in document]
        text = "\n".join(page_texts)
        assert "Éloi" in text and "Anaïs" in text
        assert "Nom:" in text
        assert r"\underline" not in text
        assert "Bonne idée" in text
        assert "“eg”" in text and "C:\\tmp" in text
        assert text.index("Thème test") < text.index("Q1") < text.index("Énoncé")
        assert text.count("Choisir") == 1
        assert text.count("une option") == 1
        assert text.count("int x = 1;") == 1
        assert "Explication :" in text and "Argument décisif." in text
        assert "Réponse :" not in text
        assert not re.search(r"(?m)^Réponse\s*$", text)
        assert "Une réponse détaillée reste visible." in text
        assert "∀" in text
        assert any(page.get_images() for page in document)
        assert any(
            "pagella" in font[3].lower() or "pagella" in font[4].lower()
            for page in document
            for font in page.get_fonts()
        )
        total_pages = len(document)
        for page_number, page_text in enumerate(page_texts, start=1):
            assert "Bilan individuel de correction : Éloi Anaïs" in page_text
            assert re.search(r"\d{2}/\d{2}/\d{4}", page_text)
            assert f"{page_number} / {total_pages}" in page_text


def test_student_report_template_has_fallback_without_data():
    """The template alone must render an explicit placeholder report."""
    template = Path("correct_ai/templates/student_report.typ").resolve()

    compiled = typst.compile(str(template), root=str(template.parent))

    with pymupdf.open(stream=compiled, filetype="pdf") as document:
        text = "\n".join(page.get_text() for page in document)
    assert "Aucune donnée de rapport fournie" in text
    assert "Non renseigné" in text


@pytest.mark.parametrize(
    ("payload", "expected_question", "expected_note"),
    [
        ({"questions": [{"titre": "Q1", "enonce": None}]}, "Q1", "Non notée"),
        ({"questions": None}, None, None),
        ({}, None, None),
    ],
)
def test_student_report_template_accepts_partial_json(
    tmp_path, payload, expected_question, expected_note
):
    """Missing and null report fields use their Typst fallbacks."""
    template = Path("correct_ai/templates/student_report.typ").resolve()
    shutil.copyfile(template, tmp_path / template.name)
    shutil.copytree(template.parent / "vendor", tmp_path / "vendor")
    (tmp_path / "data.json").write_text(json.dumps(payload), encoding="utf-8")

    compiled = typst.compile(
        str(tmp_path / template.name),
        root=str(tmp_path),
        sys_inputs={"report-data-path": "data.json"},
    )

    with pymupdf.open(stream=compiled, filetype="pdf") as document:
        text = "\n".join(page.get_text() for page in document)
    assert "Non renseigné" in text
    if expected_question:
        assert expected_question in text
    if expected_note:
        assert expected_note in text


def test_generate_student_pdf_rejects_unknown_copy(tmp_path):
    """A missing copy must not leave an empty report behind."""
    db = get_db(str(tmp_path))
    init_db(db)
    db.close()
    with pytest.raises(FileNotFoundError, match="Copie 42"):
        generate_student_pdf(42, str(tmp_path / "korektor.db"), "rapports_pdf/Copie_42.pdf")
    assert not (tmp_path / "rapports_pdf" / "Copie_42.pdf").exists()


def test_generate_student_pdf_cleans_workdir_and_preserves_output_on_typst_error(
    tmp_path, monkeypatch
):
    """A failed Typst compile removes its workdir and leaves the previous PDF intact."""
    db = get_db(str(tmp_path))
    init_db(db)
    db.execute("INSERT INTO copies (id, nom) VALUES (1, 'Doe')")
    db.commit()
    db.close()

    output = tmp_path / "rapports_pdf" / "Copie_01.pdf"
    output.parent.mkdir()
    output.write_bytes(b"previous-pdf")
    workdirs_before = set(TEMPLATE_PATH.parent.glob(".student-report-*"))

    def fail_compile(*args, **kwargs):
        raise typst.TypstError("synthetic compilation failure")

    monkeypatch.setattr("correct_ai.pdf_builder.typst.compile", fail_compile)

    with pytest.raises(RuntimeError, match="Compilation Typst impossible"):
        generate_student_pdf(1, str(tmp_path / "korektor.db"), str(output))

    assert output.read_bytes() == b"previous-pdf"
    assert set(TEMPLATE_PATH.parent.glob(".student-report-*")) == workdirs_before


def test_image_path_rejects_paths_outside_project(tmp_path):
    """Only crops located inside the project can enter the Typst work directory."""
    project = tmp_path / "project"
    workdir = tmp_path / "work"
    project.mkdir()
    workdir.mkdir()
    inside = project / "cropped_questions" / "answer.png"
    inside.parent.mkdir()
    inside.write_bytes(b"crop")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    (project / "linked.png").symlink_to(outside)

    assert _image_path(project, "cropped_questions/answer.png", workdir, 1) == "question-1.png"
    assert (workdir / "question-1.png").read_bytes() == b"crop"
    assert _image_path(project, "../outside.png", workdir, 2) == ""
    assert not (workdir / "question-2.png").exists()
    assert _image_path(project, "linked.png", workdir, 3) == ""
    assert not (workdir / "question-3.png").exists()
