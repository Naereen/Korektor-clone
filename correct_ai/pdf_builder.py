"""Build an individual Typst PDF from a Korektor SQLite project."""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import typst

from correct_ai.image_processing import is_valid_question_box

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "student_report.typ"
_QUESTION_HEADING = re.compile(r"(?m)^\s*\*\*(Q[\w.-]+)\.\*\*\s*")
_SUBJECT_MARKER = re.compile(r"\(--(Q[\w.-]+)\)")
_ATX_HEADING = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)\s*$")


def _sections(content: str, pattern: re.Pattern[str]) -> dict[str, str]:
    """Map question markers to their preceding subject or following key text."""
    matches = list(pattern.finditer(content))
    if pattern is _SUBJECT_MARKER:
        return {
            match.group(1): content[
                matches[index - 1].end() if index else 0 : match.start()
            ].strip()
            for index, match in enumerate(matches)
        }
    return {
        match.group(1): content[
            match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(content)
        ].strip()
        for index, match in enumerate(matches)
    }


def _section_titles(content: str, pattern: re.Pattern[str]) -> dict[str, list[str]]:
    """Map questions to level-two-or-lower headings that introduce them."""
    matches = list(pattern.finditer(content))
    titles = {}
    for index, match in enumerate(matches):
        start = matches[index - 1].end() if index else 0
        section = content[start : match.start()]
        headings = [
            heading.group(2).strip()
            for heading in _ATX_HEADING.finditer(section)
            if len(heading.group(1)) >= 2
        ]
        if headings:
            titles[match.group(1)] = headings
    return titles


def _strip_section_markers(content: str) -> str:
    """Remove headings and a leading question label from panel content."""
    without_headings = _ATX_HEADING.sub("", content)
    return _QUESTION_HEADING.sub("", without_headings, count=1).strip()


def _strip_repeated_statement(answer: str, statement: str) -> str:
    """Remove exact Typst markup duplicated at the start of an answer key."""
    if not statement or not answer.startswith(statement):
        return answer
    remainder = answer[len(statement) :]
    if remainder and not remainder[0].isspace():
        return answer
    return remainder.lstrip()


def _score(value: Any, total: float) -> str:
    """Format a score against the complete rubric without rounding away points."""
    return f"{float(value):g} / {total:g}" if value is not None else "Non notée"


def _score_out_of_20(value: Any, total: float) -> str:
    """Normalize a score to 20 while preserving unavailable grades."""
    if value is None or total <= 0:
        return "Non disponible"
    return f"{float(value) / total * 20:.2f} / 20"


def _markdown_to_typst(content: str) -> str:
    """Convert trusted project Markdown to Typst markup with Pandoc."""
    try:
        result = subprocess.run(
            ["pandoc", "--from=markdown", "--to=typst"],
            input=content,
            text=True,
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("Pandoc est requis pour convertir le Markdown du rapport") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or "erreur inconnue"
        raise RuntimeError(f"Conversion Markdown vers Typst impossible : {detail}") from error
    return result.stdout.strip()


def _image_path(project_dir: Path, crop_path: str | None, work_dir: Path, index: int) -> str:
    """Copy an existing in-project crop into Typst's isolated working directory."""
    if not crop_path:
        return ""
    try:
        source = (project_dir / crop_path).resolve()
        if not source.is_relative_to(project_dir) or not source.is_file():
            return ""
        destination = work_dir / f"question-{index}{source.suffix.lower()}"
        shutil.copyfile(source, destination)
    except (OSError, RuntimeError, ValueError):
        return ""
    return destination.name


def _has_usable_crop_metadata(value: str | None) -> bool:
    """Accept valid Qwen boxes and explicitly generated fallback crops."""
    try:
        boxes = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(boxes, list) and any(
        isinstance(box, dict)
        and (is_valid_question_box(box.get("bbox_2d")) or box.get("fallback") is True)
        for box in boxes
    )


def generate_student_pdf(copie_id: int, db_path: str, output_pdf_path: str) -> str:
    """Compile a student's illustrated feedback PDF and return its output path.

    Scores use the teacher's validated mark when available, otherwise the AI
    mark. The promotion mean includes copies with at least one graded question.
    Relative output paths are interpreted from the project directory.

    Raises:
        FileNotFoundError: The database or requested copy does not exist.
        RuntimeError: Typst cannot compile the report.
    """
    database_path = Path(db_path).resolve(strict=True)
    project_dir = database_path.parent
    output = Path(output_pdf_path)
    if not output.is_absolute():
        output = project_dir / output
    output = output.resolve()

    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        student = db.execute(
            "SELECT id, nom, prenom FROM copies WHERE id = ?", (copie_id,)
        ).fetchone()
        if student is None:
            raise FileNotFoundError(f"Copie {copie_id} introuvable dans {database_path}")

        rubric = db.execute(
            """SELECT b.id, b.question, b.texte_question, b.reponse_attendue, b.points,
                      c.commentaire_llm, c.commentaire_prof, c.validee_par_prof,
                      c.note_llm, c.note_prof, c.crop_image_path, c.bounding_boxes
               FROM bareme b LEFT JOIN corrections c
                 ON c.bareme_id = b.id AND c.copie_id = ? ORDER BY b.id""",
            (copie_id,),
        ).fetchall()
        max_points = sum(float(row["points"]) for row in rubric)
        subject_row = db.execute("SELECT contenu FROM sujet LIMIT 1").fetchone()
        answer_row = db.execute("SELECT contenu FROM corrige LIMIT 1").fetchone()
        subject = subject_row[0] if subject_row else ""
        answer_key = answer_row[0] if answer_row else ""
        totals = db.execute(
            """SELECT SUM(CASE WHEN validee_par_prof = 1 THEN note_prof ELSE note_llm END) AS total
               FROM corrections GROUP BY copie_id
               HAVING COUNT(CASE WHEN validee_par_prof = 1 THEN note_prof ELSE note_llm END) > 0"""
        ).fetchall()
        averages = [float(row["total"]) for row in totals]

    raw_subject_sections = _sections(subject, _SUBJECT_MARKER)
    raw_answer_sections = _sections(answer_key, _QUESTION_HEADING)
    subject_titles = _section_titles(subject, _SUBJECT_MARKER)
    answer_titles = _section_titles(answer_key, _QUESTION_HEADING)
    subject_sections = {
        question: _strip_section_markers(section)
        for question, section in raw_subject_sections.items()
    }
    answer_sections = {
        question: _strip_section_markers(section)
        for question, section in raw_answer_sections.items()
    }
    scores = [
        float(row["note_prof"] if row["validee_par_prof"] else row["note_llm"])
        for row in rubric
        if (row["note_prof"] if row["validee_par_prof"] else row["note_llm"]) is not None
    ]
    total_score = sum(scores) if scores else None

    with tempfile.TemporaryDirectory(prefix=".student-report-", dir=TEMPLATE_PATH.parent) as temp:
        work_dir = Path(temp)
        shutil.copyfile(TEMPLATE_PATH, work_dir / TEMPLATE_PATH.name)
        questions = []
        for index, row in enumerate(rubric, start=1):
            grade = row["note_prof"] if row["validee_par_prof"] else row["note_llm"]
            comment = row["commentaire_prof"] if row["validee_par_prof"] else row["commentaire_llm"]
            statement = _strip_section_markers(
                row["texte_question"]
                or subject_sections.get(row["question"])
                or "Énoncé indisponible."
            )
            answer = _strip_section_markers(
                answer_sections.get(row["question"])
                or row["reponse_attendue"]
                or "Corrigé indisponible."
            )
            statement_typst = _markdown_to_typst(statement)
            answer_typst = _markdown_to_typst(answer)
            answer_typst = _strip_repeated_statement(answer_typst, statement_typst)
            if not answer_typst:
                answer_typst = _markdown_to_typst("Corrigé indisponible.")
            questions.append(
                {
                    "titre": row["question"],
                    "section_titles": [
                        _markdown_to_typst(title)
                        for title in (
                            subject_titles.get(row["question"])
                            or answer_titles.get(row["question"])
                            or []
                        )
                    ],
                    "enonce": statement_typst,
                    "corrige": answer_typst,
                    "appreciation": comment or "Aucune appréciation.",
                    "note": _score(grade, float(row["points"])),
                    "crop_image_path": _image_path(
                        project_dir,
                        (
                            row["crop_image_path"]
                            if _has_usable_crop_metadata(row["bounding_boxes"])
                            else None
                        ),
                        work_dir,
                        index,
                    ),
                }
            )

        data = {
            "copie_id": str(copie_id),
            "nom": student["nom"] or "Non renseigné",
            "prenom": student["prenom"] or "Non renseigné",
            "note_globale": _score(total_score, max_points),
            "moyenne_promotion": (
                _score(sum(averages) / len(averages), max_points) if averages else "Non disponible"
            ),
            "note_sur_20": _score_out_of_20(total_score, max_points),
            "moyenne_sur_20": _score_out_of_20(
                sum(averages) / len(averages) if averages else None, max_points
            ),
            "titre": next(
                (
                    line.lstrip("# ").strip()
                    for line in subject.splitlines()
                    if line.startswith("# ")
                ),
                "Correction de copie",
            ),
            "questions": questions,
        }
        serialized_data = json.dumps(data, ensure_ascii=False)
        (work_dir / "data.json").write_text(serialized_data, encoding="utf-8")
        try:
            compiled_pdf = typst.compile(
                str(work_dir / TEMPLATE_PATH.name),
                root=str(work_dir),
                sys_inputs={"report-data-path": "data.json"},
            )
        except typst.TypstError as error:
            raise RuntimeError(f"Compilation Typst impossible : {error}") from error

        output.parent.mkdir(parents=True, exist_ok=True)
        staging_path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=output.parent, suffix=".pdf", delete=False
            ) as staging:
                staging_path = Path(staging.name)
                staging.write(compiled_pdf)
            os.replace(staging_path, output)
        finally:
            if staging_path is not None:
                staging_path.unlink(missing_ok=True)
    return str(output)
