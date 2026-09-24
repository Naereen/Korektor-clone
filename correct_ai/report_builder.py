"""
Génération des rapports Markdown/PDF de feedback individuel pour les étudiants.

Ce module construit, à partir des données de la base SQLite (barème + corrections
d'une copie), un rapport Markdown lisible par l'étudiant (réponse, commentaire,
note), à l'exclusion du raisonnement interne du LLM qui ne doit pas lui être
communiqué. Le rapport peut ensuite être compilé en PDF via pandoc.

Utilisation:
    from correct_ai.report_builder import build_report_markdown, report_basename

    content = build_report_markdown(copie_info, corrections, sujet_title, sujet_date)
"""

import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

REPORTS_DIRNAME = "rapports_etudiants"

_TITLE_PATTERN = re.compile(r"^.*[Tt]it[lr]e\s*:([^$]*)")
_DATE_PATTERN = re.compile(r"^.*[Dd]ate\s*:([^$]*)")


def get_reports_dir(project_dir: str) -> str:
    """Return (and create) the directory holding student feedback reports."""
    path = os.path.join(project_dir, REPORTS_DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path


def parse_sujet_header(sujet_content: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract the ``title:`` and ``date:`` HTML-comment metadata from sujet.md."""
    title = None
    date = None
    for line in (sujet_content or "").splitlines()[:10]:
        stripped = line.strip()
        if title is None:
            match = _TITLE_PATTERN.match(stripped)
            if match:
                title = re.sub(r"\s*-->\s*$", "", match.group(1)).strip()
        if date is None:
            match = _DATE_PATTERN.match(stripped)
            if match:
                date = re.sub(r"\s*-->\s*$", "", match.group(1)).strip()
        if title and date:
            break
    return title, date


def _slug(value: Any) -> str:
    """Sanitize a value into a filesystem-safe filename component."""
    text = str(value or "").strip()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^\w\-]", "", text, flags=re.UNICODE)
    return text or "NA"


def report_basename(copie_id: Any, etudiant_id: Any, nom: Any, prenom: Any) -> str:
    """Build the report base filename (without extension) for one student."""
    return f"rapport_etudiant_{_slug(copie_id)}_{_slug(etudiant_id)}_{_slug(nom)}_{_slug(prenom)}"


def _format_note(value: Any) -> str:
    """Format a note/points value for display, tolerating missing/invalid data."""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return "-"


def build_report_markdown(
    copie_info: Dict[str, Any],
    corrections: List[Dict[str, Any]],
    sujet_title: Optional[str] = None,
    sujet_date: Optional[str] = None,
) -> str:
    """Build the Markdown feedback report shown to one student.

    The LLM's internal ``raisonnement`` is intentionally omitted: only the
    student's answer, the teacher/LLM comment, and the note are included.
    """
    nom = copie_info.get("nom") or ""
    prenom = copie_info.get("prenom") or ""
    etudiant_id = copie_info.get("etudiant_id") or ""
    copie_id = copie_info.get("id")

    lines = []
    if sujet_title:
        lines.append(f"<!-- title: Rapport de correction -- {sujet_title} -->")
    if sujet_date:
        lines.append(f"<!-- date: {sujet_date} -->")
    lines.append("")
    if sujet_title:
        lines.append(f"# {sujet_title}")
        lines.append("## Rapport de correction")
    else:
        lines.append("# Rapport de correction")
    lines.append("")
    lines.append(f"**Étudiant :** {(prenom + ' ' + nom).strip() or 'Inconnu'}")
    if etudiant_id is not None:
        lines.append(f"**N° étudiant :** {etudiant_id}")
    lines.append("")
    lines.append(f"**N° copie :** {copie_id}")
    lines.append("")

    total_note = 0.0
    total_points = 0.0
    for row in corrections:
        try:
            total_note += float(row.get("note") or 0)
        except (TypeError, ValueError):
            pass
        try:
            total_points += float(row.get("points") or 0)
        except (TypeError, ValueError):
            pass

    if total_points > 0:
        sur_20 = total_note / total_points * 20
        lines.append(
            f"**Note totale : {total_note:g} / {total_points:g} ({sur_20:.2f} / 20)**"
        )
    else:
        lines.append(f"**Note totale : {total_note:g}**")
    lines.append("")
    lines.append("---")

    for row in corrections:
        question = row.get("question") or ""
        note = _format_note(row.get("note"))
        points = _format_note(row.get("points"))
        reponse = (row.get("reponses etudiant") or "").strip()
        commentaire = (row.get("commentaires eventuels du correcteur") or "").strip()

        lines.append("")
        lines.append(f"## {question}")
        lines.append("")
        lines.append(f"**Note : {note} / {points}**")
        lines.append("")
        lines.append("**Votre réponse :**")
        lines.append("")
        lines.append(reponse or "*(non répondu)*")
        lines.append("")
        lines.append("**Commentaire :**")
        lines.append("")
        lines.append(commentaire or "*(aucun commentaire)*")
        lines.append("")
        lines.append("---")

    return "\n".join(lines).rstrip() + "\n"


def compile_markdown_to_pdf(md_path: str, pdf_path: str) -> None:
    """Compile a Markdown report to PDF using strapdown2pdf, or pandoc (with the xelatex engine).

    strapdown2pdf is tried first, and if it fails, pandoc with xelatex is used.
    This script lives here: https://perso.crans.org/besson/bin/strapdown2pdf

    Raises:
        RuntimeError: if pandoc/xelatex is missing or the compilation fails.
    """
    try:
        # FIXME: document this usage of my custom PDF compilation strategy
        # See strapdown2pdf (https://perso.crans.org/besson/bin/strapdown2pdf)
        # Try to compile using strapdown2pdf first, fallback to pandoc if it fails.
        try:
            subprocess.run(
                ["strapdown2pdf", md_path],
                check=True,
                capture_output=True,
                text=True,
            )
        except:
            subprocess.run(
                ["pandoc", md_path, "-o", pdf_path, "--pdf-engine=xelatex"],
                check=True,
                capture_output=True,
                text=True,
            )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "pandoc est introuvable : installez pandoc et un moteur LaTeX (xelatex)."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Échec de la compilation PDF (pandoc) : {exc.stderr}") from exc
