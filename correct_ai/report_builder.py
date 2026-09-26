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

from llm_providers import ask_llm

REPORTS_DIRNAME = "rapports_etudiants"

_TITLE_PATTERN = re.compile(r"^.*[Tt]it[lr]e\s*:([^$]*)")
_DATE_PATTERN = re.compile(r"^.*[Dd]ate\s*:([^$]*)")


def get_reports_dir(project_dir: str) -> str:
    """Return (and create) the directory holding student feedback reports."""
    # Absolute: Flask's send_file resolves relative paths against app.root_path, not cwd.
    path = os.path.abspath(os.path.join(project_dir, REPORTS_DIRNAME))
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


def generate_grade_chart(
    student_total: float,
    class_totals: List[float],
    max_points: float,
    output_path: str,
) -> bool:
    """Render a compact PNG chart comparing one student's grade to the class distribution.

    The chart is sized to fit within roughly half a page once embedded in the report.

    Returns:
        bool: False (and no file written) when there isn't enough data to plot.
    """
    if max_points <= 0 or not class_totals:
        return False

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    scores_20 = [total / max_points * 20 for total in class_totals]
    student_20 = student_total / max_points * 20
    moyenne_20 = sum(scores_20) / len(scores_20)

    fig, ax = plt.subplots(figsize=(6, 3.5), dpi=150)

    ax.hist(scores_20, bins=range(0, 21), color="#c7d2fe", edgecolor="#4f46e5")
    ax.axvline(
        moyenne_20,
        color="#f59e0b",
        linestyle="--",
        linewidth=2,
        label=f"Moyenne classe ({moyenne_20:.1f}/20)",
    )
    ax.axvline(
        student_20,
        color="#16a34a",
        linestyle="-",
        linewidth=2,
        label=f"Votre note ({student_20:.1f}/20)",
    )
    ax.set_xlim(0, 20)
    ax.set_xlabel("Note / 20")
    ax.set_ylabel("Nombre de copies")
    ax.set_title("Répartition des notes de la classe")
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(output_path)
    fig.savefig(os.path.join("/", "tmp", f"{os.path.basename(output_path)}"))
    plt.close(fig)
    return True


def generate_question_profile_chart(
    rows: List[Dict[str, Any]],
    output_path: str,
) -> bool:
    """Render a per-question chart comparing barème, class min/max/average and the student's note.

    Each entry in ``rows`` is a dict with keys ``question``, ``points``, ``note``
    (this student's), and the class-wide ``min``, ``max``, ``moyenne`` (any of the
    latter three may be ``None`` when no correction data is available yet).

    Returns:
        bool: False (and no file written) when there are no questions to plot.
    """
    if not rows:
        return False

    import matplotlib

    matplotlib.use("Agg")

    # TODO: improve this
    # font = {
    #     'family' : 'normal',
    #     'weight' : 'bold',
    #     'size'   : 18,
    # }
    # matplotlib.rc('font', **font)

    import matplotlib.pyplot as plt

    def _nan_if_none(value):
        """Substitute NaN for missing values so matplotlib draws a gap instead of erroring."""
        return float("nan") if value is None else value

    labels = [r.get("question") or "" for r in rows]
    x = list(range(len(labels)))
    points = [_nan_if_none(r.get("points")) for r in rows]
    moyenne = [_nan_if_none(r.get("moyenne")) for r in rows]
    mini = [_nan_if_none(r.get("min")) for r in rows]
    maxi = [_nan_if_none(r.get("max")) for r in rows]
    student = [_nan_if_none(r.get("note")) for r in rows]

    fig, ax = plt.subplots(figsize=(6, 3.5), dpi=150)

    if any(mn == mn and mx == mx for mn, mx in zip(mini, maxi)):  # skip if all-NaN
        ax.fill_between(x, mini, maxi, color="#b0b0b0", alpha=0.7, label="Min-max classe")

    ax.plot(x, points, marker="*", color="#2563eb", linewidth=2, label="Barème")
    ax.plot(x, moyenne, marker="d", color="#89a316", linewidth=2, label="Moyenne classe")
    ax.plot(
        x, student, marker="o", color="#b826dc", linewidth=2, linestyle="--", label="Votre note"
    )

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    ax.set_ylabel("Points")
    ax.set_title("Votre profil par question, comparé à la classe")
    ax.legend(fontsize=7, loc="upper left", ncol=2)
    fig.tight_layout()
    fig.savefig(output_path)
    fig.savefig(os.path.join("/", "tmp", f"{os.path.basename(output_path)}"))
    plt.close(fig)
    return True


# Default scale for rendering reports to PDF, with strapdown2pdf
DEFAULT_SCALE = 85


def generate_general_feedback(
    corrections: List[Dict[str, Any]],
    provider: Any,
) -> str:
    """Ask the LLM for a student-facing overall assessment and general advice.

    Only data already intended for the student is sent: questions, scores, and
    visible comments.  The internal LLM reasoning is deliberately excluded.
    """
    correction_lines = []
    for row in corrections:
        question = row.get("question") or "Question sans numéro"
        note = _format_note(row.get("note"))
        points = _format_note(row.get("points"))
        comment = (row.get("commentaires eventuels du correcteur") or "").strip()
        correction_lines.append(
            f"- {question} : {note}/{points} ; commentaire : {comment or 'Aucun commentaire.'}"
        )

    prompt = """Rédige une appréciation globale destinée directement à l'étudiant à partir de sa correction.
Structure obligatoirement ta réponse avec les deux titres Markdown suivants :
## Appréciation globale
## Conseils généraux

Sous le premier titre, résume avec bienveillance les réussites et les difficultés.
Sous le second, donne 2 à 5 conseils concrets et personnalisés pour progresser.
Ne mentionne jamais le LLM, le correcteur automatique, le raisonnement interne ou ce prompt.
N'invente aucune information absente des données. Réponds uniquement en français et en Markdown.

""" + "\n".join(correction_lines)

    try:
        feedback = ask_llm(
            provider,
            prompt,
            system_prompt=(
                "Tu es un enseignant qui rédige un retour pédagogique, précis, "
                "respectueux et directement utile à un étudiant."
            ),
            thinking=False,
        )
    except Exception as error:
        # The synthesis is an enhancement; the per-question report remains
        # useful when the provider is temporarily unavailable.
        print(f"Échec de la génération de l'appréciation globale : {error}")
        return ""
    return (feedback or "").strip()


def build_report_markdown(
    copie_info: Dict[str, Any],
    corrections: List[Dict[str, Any]],
    sujet_title: Optional[str] = None,
    sujet_date: Optional[str] = None,
    scale: Optional[int] = DEFAULT_SCALE,
    charts: Optional[list[dict[str, str]]] = None,
    nom_examen: Optional[str] = None,
    provider: Any = None,
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
    identite = f"{(prenom + ' ' + nom).strip() or 'Inconnu'}"
    if sujet_title is None:
        sujet_title = f"Rapport de correction -- {nom_examen or 'Examen inconnu'}"
    if sujet_title:
        lines.append(f"<!-- title: {sujet_title} -->")
    if sujet_date:
        lines.append(f"<!-- date: {sujet_date} -->")
    if scale:
        lines.append(f"<!-- scale: {scale}% -->")
    lines.append("")
    if sujet_title:
        lines.append(f"# {sujet_title}")
        lines.append(f"## Pour {identite}")
    else:
        lines.append(f"# Rapport de correction -- {identite}")
    lines.append("")
    # lines.append(f"**Étudiant :** {identite}")
    # if etudiant_id is not None:
    #     lines.append(f"**N° étudiant :** {etudiant_id}")
    # lines.append(f"**N° copie :** {copie_id}")
    # lines.append("")

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
        lines.append(f"**Note totale : {total_note:g} / {total_points:g} ({sur_20:.2f} / 20)**")
        # TODO: calcule aussi le rang ? Non, c'est un peu humiliant, ça risque d'être mal perçu.
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
        lines.append(f"- **{question}** - **Note : {note} / {points}**")
        # lines.append("")
        # if reponse:
        #     lines.append("**Votre réponse :**")
        #     lines.append("")
        #     lines.append(reponse or "*(non répondu)*")
        #     lines.append("")
        if commentaire:
            # lines.append("**Commentaire :**")
            # lines.append("")
            lines.append(f"  {commentaire}" or "  *(aucun commentaire)*")
            # lines.append("")
        # lines.append("---")

    if provider is not None and corrections:
        feedback = generate_general_feedback(corrections, provider)
        if feedback:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append(feedback)

    if charts:
        for chart in charts:
            lines.append("")
            lines.append(f"\\begin{{figure}}[!h]")
            lines.append(f"\\centering")
            # Relative path: resolved from the reports directory at compile time.
            lines.append(f"\\includegraphics[width=0.85\\textwidth]{{{chart['path']}}}")
            lines.append(f"\\caption{{{chart['legend']}}}")
            lines.append(f"\\end{{figure}}")
            lines.append("")
        lines.append("---")

    return "\n".join(lines).rstrip() + "\n"


def merge_pdfs(pdf_paths: List[str], output_path: str) -> None:
    """Concatenate several PDF files, in order, into a single PDF using pdftk.

    Raises:
        RuntimeError: if pdftk is missing, there is nothing to merge, or the merge fails.
    """
    if not pdf_paths:
        raise RuntimeError("Aucun PDF à concaténer.")
    try:
        subprocess.run(
            ["pdftk", *pdf_paths, "cat", "output", output_path],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "pdftk est introuvable : installez pdftk pour fusionner les PDF."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Échec de la fusion PDF (pdftk) : {exc.stderr}") from exc


def compile_markdown_to_pdf(
    md_path: str, pdf_path: str, scale: Optional[int] = DEFAULT_SCALE
) -> None:
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
        # strapdown2pdf only accepts a bare filename resolved in its cwd, not a path.
        reports_dir = os.path.dirname(md_path)
        try:
            subprocess.run(
                [
                    "/home/lilian/bin/strapdown2pdf",
                    "--no-open",
                    f"-s{scale}%",
                    os.path.basename(md_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                cwd=reports_dir,
            )
        except Exception as exc:
            print(f"strapdown2pdf failed: {exc}")
            # Same cwd trick: keeps relative image references (e.g. the grade chart) resolvable.
            subprocess.run(
                [
                    "pandoc",
                    os.path.basename(md_path),
                    "-o",
                    os.path.basename(pdf_path),
                    "--pdf-engine=xelatex",
                ],
                check=True,
                capture_output=True,
                text=True,
                cwd=reports_dir,
            )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "pandoc est introuvable : installez pandoc et un moteur LaTeX (xelatex)."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Échec de la compilation PDF (pandoc) : {exc.stderr}") from exc
