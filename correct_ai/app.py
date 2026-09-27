"""Korektor's Flask web application and workflow orchestration layer.

This module owns the browser-facing application.  It exposes HTML routes for
the import, split, OCR, correction, validation, and reporting screens, plus
JSON endpoints consumed by those screens.  The application coordinates the
PDF utilities, SQLite persistence layer, OCR/correction batch modules, and
the long-running background job state shown in the browser.

The main architectural boundaries are ``ProjectConfig`` for project paths,
``BatchJob`` for asynchronous batch execution, and Flask route handlers for
the HTTP boundary.  Domain work remains in the sibling modules so that the
web layer can be exercised with Flask's test client without requiring a live
server.  The file can be imported as a package module or launched directly
with ``uv run python correct_ai/app.py``.
"""

import base64
import collections
import copy
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unicodedata
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pymupdf
from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for
from pypdf import PdfReader, PdfWriter

# Import local modules when the file is launched directly.
APP_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(APP_MODULE_DIR)
sys.path.insert(0, APP_MODULE_DIR)
sys.path.insert(0, PROJECT_ROOT)

# import batch_correction
import batch_correction_v2
import batch_extraction
import database
import report_builder
from database import get_all_extractions
from database import get_bareme as db_get_bareme
from database import get_corrige as db_get_corrige
from database import get_db
from database import get_sujet as db_get_sujet
from llm_providers import DEFAULT_PROVIDER, PROVIDERS, get_provider, set_default_provider

from correct_ai.pdf_builder import generate_student_pdf

# This module is deliberately an orchestration layer rather than a domain layer.
# Routes translate browser requests into calls to the PDF, OCR, correction, and
# reporting modules; those modules own the detailed business algorithms.

# --- Configuration & Path Management ---
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.dirname(APP_ROOT)  # Go up to project root (where code is)

CONFIG_FILE = os.path.join(APP_ROOT, ".correctai_config.json")
SPLIT_SETTINGS_FILENAME = ".korektor_split.json"


class ProjectConfig:
    """Hold the currently selected project and centralize its file layout."""

    def __init__(self):
        """Initialize paths and load persisted project configuration."""
        self.project_dir = os.path.join(APP_ROOT, "exemple")
        self.project_name = "exemple"
        self.inference_provider = DEFAULT_PROVIDER
        self.load()

    @staticmethod
    def infer_project_name(project_dir):
        """Infer a display name from sujet.md or the project directory name."""
        sujet_path = os.path.join(project_dir, "sujet.md")
        try:
            with open(sujet_path, "r", encoding="utf-8") as sujet_file:
                first_lines = []
                for _ in range(10):
                    line = sujet_file.readline()
                    if not line:
                        break
                    first_lines.append(line)
        except (FileNotFoundError, OSError, UnicodeError):
            first_lines = []

        title_pattern = re.compile(r"^.*[Tt]it[lr]e\s*:([^$]*)")
        markdown_pattern = re.compile(r"^#\s+([^$]*)")
        for line in first_lines:
            match = title_pattern.match(line.strip()) or markdown_pattern.match(line.strip())
            if match:
                title = re.sub(r"\s*-->\s*$", "", match.group(1)).strip()
                if title:
                    return title

        return os.path.basename(os.path.normpath(project_dir)) or project_dir

    def load(self):
        """Load the project directory from the JSON configuration file."""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    self.project_dir = data.get("project_dir", APP_ROOT)
                    self.project_name = data.get(
                        "project_name", self.infer_project_name(self.project_dir)
                    )
                    provider_name = data.get("inference_provider", DEFAULT_PROVIDER)
                    self.inference_provider = (
                        provider_name if provider_name in PROVIDERS else DEFAULT_PROVIDER
                    )
            except:
                pass

    def save(self):
        """Persist the current project directory to the JSON configuration file."""
        with open(CONFIG_FILE, "w") as f:
            json.dump(
                {
                    "project_dir": self.project_dir,
                    "project_name": self.project_name,
                    "inference_provider": self.inference_provider,
                },
                f,
                ensure_ascii=False,
            )

    def get_path(self, *args):
        """Return a path formed by joining the project directory and components."""
        return os.path.join(self.project_dir, *args)

    def get_output_dir(self):
        """Return and create the directory containing anonymized copies."""
        # The directory is shared by the splitter, OCR workflow, and extraction
        # batch, so keeping its name in one place prevents path drift between UI
        # routes and background workers.
        # User requested subfolders for correction: 'corrections' or keep 'copies_anonymes_validees'
        # Let's keep the legacy name for continuity, or strictly follow "sub-dossiers".
        # Current valid data is in 'copies_anonymes_validees'.
        path = self.get_path("copies_anonymes_validees")
        os.makedirs(path, exist_ok=True)
        return path

    def get_sorted_pdf_path(self):
        """Return the sorted booklet PDF path used by the splitter."""
        return self.get_path("copiesA4_sorted.pdf")

    def get_pages_per_copy(self):
        """Return the page count used for each split-view segment."""
        try:
            with open(self.get_path(SPLIT_SETTINGS_FILENAME), encoding="utf-8") as settings_file:
                pages_per_copy = int(json.load(settings_file)["pages_per_copy"])
                if pages_per_copy > 0:
                    return pages_per_copy
        except (FileNotFoundError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass
        return 4

    def set_pages_per_copy(self, pages_per_copy):
        """Persist the page count used for each split-view segment."""
        with open(self.get_path(SPLIT_SETTINGS_FILENAME), "w", encoding="utf-8") as settings_file:
            json.dump({"pages_per_copy": pages_per_copy}, settings_file)


config = ProjectConfig()
set_default_provider(config.inference_provider)

# Flask is created once at import time so tests can use the same route table
# through ``app.test_client()`` without starting a development server.
app = Flask(__name__, template_folder="templates", static_folder="static")
with closing(get_db(config.project_dir)) as startup_db:
    database.init_db(startup_db)


def get_repository_context():
    """Return safe, display-ready metadata for the shared application footer."""
    fallback = {
        "commit_short": "inconnu",
        "commit_message": "Version locale",
        "commit_date": "",
        "commit_year": "2026",
        "commit_url": None,
        "repository_url": "https://github.com/Naereen/Korektor-clone",
    }
    try:
        commit = subprocess.run(
            ["git", "log", "-1", "--format=%h%x1f%s%x1f%ad", "--date=iso-strict"],
            cwd=APP_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        ).stdout.strip().split("\x1f")
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=APP_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        ).stdout.strip()
        repository_url = remote.rstrip("/")
        if repository_url.startswith("git@github.com:"):
            repository_url = "https://github.com/" + repository_url.split(":", 1)[1]
        if repository_url.endswith(".git"):
            repository_url = repository_url[:-4]
        if len(commit) == 3 and "github.com/" in repository_url:
            commit_timestamp = datetime.fromisoformat(commit[2])
            fallback.update(
                {
                    "commit_short": commit[0],
                    "commit_message": commit[1],
                    "commit_date": commit_timestamp.strftime("À %H:%M le %d/%m/%Y"),
                    "commit_year": str(commit_timestamp.year),
                    "commit_url": f"{repository_url}/commit/{commit[0]}",
                    "repository_url": repository_url,
                }
            )
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return fallback


@app.context_processor
def inject_project_context():
    """Expose the active project directory to every rendered template."""
    return {
        "current_project_dir": config.project_dir,
        "current_project_name": config.project_name,
        "repository": get_repository_context(),
    }


APP_ICON_PATH = os.path.join(APP_ROOT, "favicon-512x512.png")


@app.route("/favicon-512x512.png")
def app_icon():
    """Serve the application icon stored at the project root."""
    return send_file(APP_ICON_PATH, mimetype="image/png")


# The split-view document is a read-only process-level cache.  It is invalidated
# whenever the active project or the imported PDF changes.
splitter_doc = None


def get_splitter_doc():
    """Return the cached split-view PDF, opening it when necessary."""
    global splitter_doc
    path = config.get_sorted_pdf_path()
    if not os.path.exists(path):
        return None
    if splitter_doc is None or splitter_doc.is_closed:
        splitter_doc = pymupdf.open(path)
    return splitter_doc


# --- Settings Route ---
# Project selection changes the root of every subsequent file and database
# operation, so the cached PDF handle must be closed before switching projects.
@app.route("/settings", methods=["GET", "POST"])
def settings():
    """Render or update the project settings page."""
    if request.method == "POST":
        new_dir = request.form.get("project_dir")
        provider_name = request.form.get("inference_provider", config.inference_provider)
        if provider_name not in PROVIDERS:
            return render_template(
                "settings.html",
                error="Fournisseur d'inférence invalide",
                current_dir=config.project_dir,
                current_provider=config.inference_provider,
            )
        if new_dir and os.path.isdir(new_dir):
            config.project_dir = new_dir
            config.project_name = config.infer_project_name(new_dir)
            config.inference_provider = provider_name
            set_default_provider(provider_name)
            config.save()
            # Reset global state
            global splitter_doc
            if splitter_doc:
                splitter_doc.close()
            splitter_doc = None
            return redirect(url_for("index"))
        else:
            return render_template(
                "settings.html",
                error="Dossier invalide",
                current_dir=config.project_dir,
                current_provider=config.inference_provider,
            )
    return render_template(
        "settings.html",
        current_dir=config.project_dir,
        current_provider=config.inference_provider,
    )


# --- ROUTES: Navigation ---


@app.route("/")
def index():
    """Redirect the root URL to the import workflow."""
    return redirect("/import")


@app.route("/import")
def import_page():
    """Render the project import page."""
    return render_template("import.html", active_page="import", current_dir=config.project_dir)


@app.route("/verify_bareme")
def verify_bareme_page():
    """Render the file-backed rubric review and editing screen."""
    return render_template("verify_bareme.html", active_page="verify_bareme")


# --- API ROUTES: Project Management ---


@app.route("/api/project/set", methods=["POST"])
def api_project_set():
    """Set the active project directory through the JSON API."""
    """Définit le dossier du projet."""
    data = request.get_json(silent=True) or {}
    new_dir = data.get("project_dir", "")
    if new_dir and os.path.isdir(new_dir):
        config.project_dir = new_dir
        config.project_name = config.infer_project_name(new_dir)
        config.save()
        global splitter_doc
        if splitter_doc:
            splitter_doc.close()
        splitter_doc = None
        return jsonify(
            {
                "success": True,
                "project_dir": config.project_dir,
                "project_name": config.project_name,
            }
        )
    return jsonify({"success": False, "error": "Dossier invalide"}), 400


@app.route("/api/project/check")
def api_project_check():
    """Return project file readiness and copy counts as JSON."""
    """Vérifie la structure du dossier projet."""
    project_dir = config.project_dir
    files = {
        "sujet.md": os.path.exists(os.path.join(project_dir, "sujet.md")),
        "corrige.md": os.path.exists(os.path.join(project_dir, "corrige.md")),
        "bareme.csv": os.path.exists(os.path.join(project_dir, "bareme.csv")),
        "liste-etudiants.csv": os.path.exists(os.path.join(project_dir, "liste-etudiants.csv")),
        "correspondance.csv": os.path.exists(os.path.join(project_dir, "correspondance.csv")),
        "copiesA3.pdf": os.path.exists(os.path.join(project_dir, "copiesA3.pdf")),
        "copiesA4_sorted.pdf": os.path.exists(os.path.join(project_dir, "copiesA4_sorted.pdf")),
    }
    # Count both generated PDFs and extracted-copy directories: they represent
    # different stages of the workflow and can legitimately coexist.
    copies_dir = os.path.join(project_dir, "copies_anonymes_validees")
    nb_pdfs = 0
    nb_extractions = 0
    if os.path.isdir(copies_dir):
        for f in os.listdir(copies_dir):
            if f.startswith("Copie_") and f.endswith(".pdf"):
                nb_pdfs += 1
            if f.startswith("Copie_") and os.path.isdir(os.path.join(copies_dir, f)):
                nb_extractions += 1

    return jsonify(
        {
            "project_dir": project_dir,
            "files": files,
            "nb_copies_pdf": nb_pdfs,
            "nb_extractions": nb_extractions,
        }
    )


@app.route("/api/fs/browse")
def api_fs_browse():
    """List subdirectories of a server-side path, for the project folder picker."""
    requested = request.args.get("path") or config.project_dir
    path = os.path.abspath(os.path.expanduser(requested))

    # Fall back to the closest existing readable ancestor directory.
    while not os.path.isdir(path):
        parent = os.path.dirname(path)
        if parent == path:
            path = os.path.expanduser("~")
            break
        path = parent

    dirs = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir():
                        dirs.append(entry.name)
                except OSError:
                    continue
    except OSError as e:
        return jsonify({"error": str(e)}), 400

    dirs.sort(key=str.lower)
    parent = os.path.dirname(path)

    return jsonify(
        {
            "path": path,
            "parent": parent if parent != path else None,
            "dirs": dirs,
        }
    )


@app.route("/api/bareme/generate", methods=["POST"])
def api_bareme_generate():
    """Run subject analysis and return the generated rubric as JSON."""
    """Génère le barème via subject_analysis.py."""
    import subprocess

    try:
        result = subprocess.run(
            [
                sys.executable,
                os.path.join(APP_ROOT, "correct_ai", "subject_analysis.py"),
                "--project-dir",
                config.project_dir,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=APP_ROOT,
        )
        print(result.stdout)
        if result.returncode == 0:
            # Lire le barème généré
            bareme_path = os.path.join(config.project_dir, "bareme.csv")
            bareme_data = []
            if os.path.exists(bareme_path):
                with open(bareme_path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    bareme_data = list(reader)
            return jsonify({"success": True, "bareme": bareme_data, "output": result.stdout})
        else:
            return (
                jsonify({"success": False, "error": result.stderr or result.stdout}),
                500,
            )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def read_bareme_editor(path):
    """Read a rubric without flattening quoted multiline CSV fields."""
    with open(path, "rb") as file:
        raw = file.read()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    fields = reader.fieldnames
    if not fields or len(fields) != len(set(fields)) or "question" not in fields:
        raise ValueError("En-têtes CSV absents, dupliqués ou sans colonne question")
    rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("Le CSV contient des lignes dont le nombre de colonnes est incorrect")
    return raw, fields, rows


@app.route("/api/bareme/editor", methods=["GET", "PUT"])
def api_bareme_editor():
    """Expose the active project's CSV rubric with optimistic concurrency."""
    path = config.get_path("bareme.csv")
    try:
        raw, fields, rows = read_bareme_editor(path)
    except FileNotFoundError:
        return jsonify({"error": "Aucun barème dans ce projet. Générez-le depuis Import."}), 404
    except (OSError, UnicodeError, csv.Error, ValueError) as error:
        return jsonify({"error": f"Impossible de lire le barème : {error}"}), 422

    revision = hashlib.sha256(raw).hexdigest()
    if request.method == "GET":
        return jsonify({"fields": fields, "rows": rows, "revision": revision})

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("revision") != revision:
        return jsonify({"error": "Le barème a changé. Rechargez la page avant d'enregistrer."}), 409
    incoming = data.get("rows")
    if data.get("fields") != fields or not isinstance(incoming, list) or not incoming:
        return jsonify({"error": "Colonnes modifiées ou liste de questions vide."}), 400

    seen = set()
    for number, row in enumerate(incoming, start=1):
        if (
            not isinstance(row, dict)
            or set(row) != set(fields)
            or any(not isinstance(value, str) for value in row.values())
        ):
            return (
                jsonify({"error": f"La ligne {number} ne correspond pas aux colonnes du CSV."}),
                400,
            )
        question = row["question"].strip()
        if not question or question.casefold() in seen:
            return (
                jsonify({"error": f"Identifiant de question manquant ou répété (ligne {number})."}),
                400,
            )
        seen.add(question.casefold())
        if "points" in fields:
            try:
                points = Decimal(row["points"].strip())
                if not points.is_finite() or points < 0:
                    raise ValueError
            except (InvalidOperation, ValueError):
                return jsonify({"error": f"Points invalides pour {question}."}), 400
        if "qcm (oui/non)" in fields and row["qcm (oui/non)"].strip().casefold() not in (
            "oui",
            "non",
        ):
            return jsonify({"error": f"QCM : utilisez Oui ou Non pour {question}."}), 400

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=fields, lineterminator="\r\n" if b"\r\n" in raw else "\n"
    )
    writer.writeheader()
    writer.writerows(incoming)
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    temp_path = None
    db = None
    try:
        if os.path.isfile(config.get_path("korektor.db")):
            if not {"qcm (oui/non)", "réponse attendue", "points"}.issubset(fields):
                return jsonify({"error": "Colonnes de correction manquantes dans le CSV."}), 400
            db = get_db(config.project_dir)
            db.execute("BEGIN IMMEDIATE")
            old_questions = db.execute("SELECT id, question FROM bareme").fetchall()
            new_questions = {row["question"].strip() for row in incoming}
            removed = [entry for entry in old_questions if entry["question"] not in new_questions]
            for entry in removed:
                if db.execute(
                    "SELECT 1 FROM corrections WHERE bareme_id = ? LIMIT 1", (entry["id"],)
                ).fetchone():
                    return (
                        jsonify(
                            {"error": f"La question {entry['question']} a déjà des corrections."}
                        ),
                        409,
                    )

            sujet = db.execute("SELECT contenu FROM sujet LIMIT 1").fetchone()
            sujet_content = sujet[0] if sujet else ""
            sujet_path = config.get_path("sujet.md")
            if not sujet_content and os.path.isfile(sujet_path):
                with open(sujet_path, encoding="utf-8") as file:
                    sujet_content = file.read()
            parts = re.split(r"\(--(Q[\w.\-]+)\)", sujet_content) if sujet_content else []
            question_texts = {
                parts[index + 1]: parts[index].strip() for index in range(0, len(parts) - 1, 2)
            }
            for entry in removed:
                db.execute("DELETE FROM bareme WHERE id = ?", (entry["id"],))
            for row in incoming:
                db.execute(
                    """INSERT INTO bareme
                       (question, texte_question, type_qcm, reponse_attendue, points)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(question) DO UPDATE SET
                         texte_question = COALESCE(NULLIF(excluded.texte_question, ''), bareme.texte_question),
                         type_qcm = excluded.type_qcm,
                         reponse_attendue = excluded.reponse_attendue,
                         points = excluded.points""",
                    (
                        row["question"].strip(),
                        question_texts.get(row["question"].strip(), ""),
                        row["qcm (oui/non)"].strip().casefold() == "oui",
                        row["réponse attendue"],
                        row["points"].strip(),
                    ),
                )
        with tempfile.NamedTemporaryFile(
            dir=config.project_dir, prefix=".bareme-", delete=False
        ) as file:
            temp_path = file.name
            file.write(buffer.getvalue().encode(encoding))
        os.replace(temp_path, path)
        if db:
            db.commit()
    except (OSError, sqlite3.Error) as error:
        if db:
            db.rollback()
        return jsonify({"error": f"Écriture du barème impossible : {error}"}), 500
    finally:
        if db:
            db.close()
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)

    return jsonify(
        {
            "success": True,
            "revision": hashlib.sha256(buffer.getvalue().encode(encoding)).hexdigest(),
        }
    )


# --- UTILS: Student Loading ---
@dataclass(frozen=True)
class Student:
    """Typed student metadata stored in the active project's roster CSV."""

    id: str
    name: str
    firstname: str
    group: str = ""
    email: str = ""

    def to_dict(self):
        """Return the public API representation of this student."""
        return {
            "id": self.id,
            "name": self.name,
            "firstname": self.firstname,
            "group": self.group,
            "email": self.email,
        }


def _normalize_student_header(value):
    normalized = unicodedata.normalize("NFKD", value or "")
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", without_accents.casefold())


def _student_column_indexes(headers):
    aliases = {
        "id": {"id", "num", "numero", "netudiant", "numeroetudiant", "idetudiant", "identifiant"},
        "name": {"nom", "nomdefamille"},
        "firstname": {"prenom", "prenomdus", "firstname"},
        "group": {"td", "groupe", "group", "classe", "class"},
        "email": {"email", "mail", "courriel"},
    }
    normalized_headers = [_normalize_student_header(header) for header in headers]
    indexes = {}
    for field, candidates in aliases.items():
        indexes[field] = next(
            (index for index, header in enumerate(normalized_headers) if header in candidates),
            None,
        )
    return indexes


def _read_student_roster():
    """Read roster rows while retaining the source CSV columns and preamble."""
    csv_path = config.get_path("liste-etudiants.csv")
    if not os.path.exists(csv_path):
        headers = ["N° étudiant", "Nom", "Prénom", "Groupe", "Email"]
        return "", headers, ";", _student_column_indexes(headers), []

    with open(csv_path, "r", newline="", encoding="utf-8-sig") as roster_file:
        lines = roster_file.readlines()

    header_index = None
    delimiter = ";"
    indexes = {}
    for index, line in enumerate(lines):
        for candidate_delimiter in (";", ","):
            fields = next(csv.reader([line], delimiter=candidate_delimiter), [])
            candidate_indexes = _student_column_indexes(fields)
            if all(candidate_indexes[field] is not None for field in ("id", "name", "firstname")):
                header_index = index
                delimiter = candidate_delimiter
                indexes = candidate_indexes
                break
        if header_index is not None:
            break

    if header_index is None:
        raise ValueError("En-tête étudiant introuvable dans liste-etudiants.csv")

    reader = csv.reader(lines[header_index:], delimiter=delimiter)
    headers = next(reader)
    indexes = _student_column_indexes(headers)
    entries = []
    for values in reader:
        values = values[:len(headers)] + [""] * max(0, len(headers) - len(values))
        student_id = values[indexes["id"]].strip()
        name = values[indexes["name"]].strip()
        if not student_id or not name:
            continue
        student = Student(
            id=student_id,
            name=name,
            firstname=values[indexes["firstname"]].strip(),
            group=values[indexes["group"]].strip() if indexes["group"] is not None else "",
            email=values[indexes["email"]].strip() if indexes["email"] is not None else "",
        )
        entries.append((student, values))

    return "".join(lines[:header_index]), headers, delimiter, indexes, entries


def _ensure_student_columns(headers):
    """Add optional fields when an older roster does not yet contain them."""
    indexes = _student_column_indexes(headers)
    if indexes["group"] is None:
        headers.append("Groupe")
    if indexes["email"] is None:
        headers.append("Email")
    return _student_column_indexes(headers)


def _write_student_roster(prefix, headers, delimiter, rows):
    """Atomically replace the roster, retaining its preamble and extra columns."""
    csv_path = config.get_path("liste-etudiants.csv")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=os.path.dirname(csv_path),
            prefix=".students-", delete=False,
        ) as roster_file:
            temp_path = roster_file.name
            roster_file.write(prefix)
            writer = csv.writer(roster_file, delimiter=delimiter, lineterminator="\n")
            writer.writerow(headers)
            writer.writerows(rows)
            roster_file.flush()
            os.fsync(roster_file.fileno())
        os.replace(temp_path, csv_path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _student_from_payload(data) -> tuple[Student | None, str | None]:
    """Validate and normalize a student payload from an API request."""
    if not isinstance(data, dict):
        return None, "Les données de l’étudiant sont invalides."

    values = {}
    limits = {"id": 64, "name": 100, "firstname": 100, "group": 100, "email": 254}
    for field, limit in limits.items():
        value = data.get(field, "")
        if not isinstance(value, str):
            return None, f"Le champ {field} doit être du texte."
        values[field] = value.strip()
        if len(values[field]) > limit:
            return None, f"Le champ {field} dépasse {limit} caractères."

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", values["id"]):
        return None, "L’identifiant doit contenir 1 à 64 lettres, chiffres, points, tirets ou tirets bas."
    if not values["name"] or not values["firstname"]:
        return None, "Le nom et le prénom sont obligatoires."
    if values["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", values["email"]):
        return None, "L’adresse e-mail n’est pas valide."
    return Student(**values), None


def load_students():
    """Load student metadata from the project's CSV file."""
    try:
        entries = _read_student_roster()[4]
    except (OSError, ValueError, csv.Error) as error:
        app.logger.warning("Impossible de lire le roster étudiant : %s", error)
        return []

    students = []
    for student, _ in entries:
        value = student.to_dict()
        value["td"] = student.group
        value["label"] = f"{student.name} {student.firstname} ({student.id})"
        students.append(value)
    return sorted(students, key=lambda student: student["label"].casefold())


@app.route("/students")
def students_page():
    """Render the active project's student roster manager."""
    return render_template("students.html", active_page="students")


@app.route("/api/students", methods=["GET", "POST"])
def api_students():
    """List students or create one in the active project's CSV roster."""
    if request.method == "GET":
        try:
            entries = _read_student_roster()[4]
            students = [student.to_dict() for student, _ in entries]
            students.sort(key=lambda student: (student["name"].casefold(), student["firstname"].casefold()))
            return jsonify(students)
        except (OSError, ValueError, csv.Error):
            app.logger.exception("Impossible de lire la liste des étudiants")
            return jsonify({"error": "Lecture de la liste des étudiants impossible."}), 500

    student, error = _student_from_payload(request.get_json(silent=True))
    if error or student is None:
        return jsonify({"error": error or "Les données de l’étudiant sont invalides."}), 400
    try:
        prefix, headers, delimiter, indexes, entries = _read_student_roster()
        if any(current.id.casefold() == student.id.casefold() for current, _ in entries):
            return jsonify({"error": "Cet identifiant étudiant existe déjà."}), 409
        indexes = _ensure_student_columns(headers)
        row = [""] * len(headers)
        for field in ("id", "name", "firstname", "group", "email"):
            if indexes[field] is not None:
                row[indexes[field]] = getattr(student, field)
        rows = [values + [""] * max(0, len(headers) - len(values)) for _, values in entries]
        rows.append(row)
        _write_student_roster(prefix, headers, delimiter, rows)
        return jsonify(student.to_dict()), 201
    except (OSError, ValueError, csv.Error):
        app.logger.exception("Impossible d’ajouter l’étudiant")
        return jsonify({"error": "Enregistrement de l’étudiant impossible."}), 500


@app.route("/api/students/<student_id>", methods=["PUT", "DELETE"])
def api_student(student_id):
    """Update or delete one student from the active project's CSV roster."""
    try:
        prefix, headers, delimiter, indexes, entries = _read_student_roster()
    except (OSError, ValueError, csv.Error):
        app.logger.exception("Impossible de lire la liste des étudiants")
        return jsonify({"error": "Lecture de la liste des étudiants impossible."}), 500

    target_index = next(
        (index for index, (student, _) in enumerate(entries) if student.id == student_id), None
    )
    if target_index is None:
        return jsonify({"error": "Étudiant introuvable."}), 404

    if request.method == "DELETE":
        entries.pop(target_index)
        try:
            _write_student_roster(prefix, headers, delimiter, [values for _, values in entries])
            return jsonify({"success": True})
        except OSError:
            app.logger.exception("Impossible de supprimer l’étudiant")
            return jsonify({"error": "Suppression de l’étudiant impossible."}), 500

    student, error = _student_from_payload(request.get_json(silent=True))
    if error or student is None:
        return jsonify({"error": error or "Les données de l’étudiant sont invalides."}), 400
    if student.id != student_id:
        return jsonify({"error": "L’identifiant ne peut pas être modifié."}), 400
    if any(
        index != target_index and current.id.casefold() == student.id.casefold()
        for index, (current, _) in enumerate(entries)
    ):
        return jsonify({"error": "Cet identifiant étudiant existe déjà."}), 409

    indexes = _ensure_student_columns(headers)
    rows = []
    for index, (current, values) in enumerate(entries):
        row = values + [""] * max(0, len(headers) - len(values))
        if index == target_index:
            for field in ("name", "firstname", "group", "email"):
                if indexes[field] is not None:
                    row[indexes[field]] = getattr(student, field)
        rows.append(row)
    try:
        _write_student_roster(prefix, headers, delimiter, rows)
        return jsonify(student.to_dict())
    except OSError:
        app.logger.exception("Impossible de modifier l’étudiant")
        return jsonify({"error": "Enregistrement des modifications impossible."}), 500


# --- UTILS: A3 Split Logic (Refactored from sortpdf.py) ---
def process_a3_split(input_stream, output_path, sort_booklet=True):
    """Split booklet pages into A4 pages and optionally reorder them."""
    # A booklet scan contains two logical A4 pages on each physical page.  The
    # split is done with page boxes, avoiding rasterization and preserving PDF
    # text/image quality for the later OCR step.
    reader = PdfReader(input_stream)
    writer = PdfWriter()

    total_pages = len(reader.pages)
    chunk_size = 2 if sort_booklet else 1

    for i in range(0, total_pages, chunk_size):
        chunk_end = min(i + chunk_size, total_pages)
        current_chunk_pages = []

        for j in range(i, chunk_end):
            original_page = reader.pages[j]
            width = original_page.mediabox.width
            height = original_page.mediabox.height
            mid_width = width / 2

            llx = float(original_page.mediabox.left)
            lly = float(original_page.mediabox.bottom)
            ury = float(original_page.mediabox.top)

            page_left = copy.deepcopy(original_page)
            page_left.mediabox.upper_right = (llx + mid_width, ury)

            page_right = copy.deepcopy(original_page)
            page_right.mediabox.lower_left = (llx + mid_width, lly)

            current_chunk_pages.append(page_left)
            current_chunk_pages.append(page_right)

        if sort_booklet and len(current_chunk_pages) == 4:
            # Reorder booklet reading order: the physical scan is commonly
            # [page 4, page 1, page 2, page 3], while the validator expects
            # [page 1, page 2, page 3, page 4].
            p4 = current_chunk_pages[0]
            p1 = current_chunk_pages[1]
            p2 = current_chunk_pages[2]
            p3 = current_chunk_pages[3]
            current_chunk_pages = [p1, p2, p3, p4]

        for p in current_chunk_pages:
            writer.add_page(p)

    with open(output_path, "wb") as f:
        writer.write(f)


# --- ROUTES: Navigation ---


@app.route("/split")
def split_page():
    """Render the booklet split and student assignment page."""
    doc = get_splitter_doc()
    if not doc:
        return "Veuillez d'abord importer un document (Étape 1).", 404

    pages_per_copy = config.get_pages_per_copy()
    total_items = (len(doc) + pages_per_copy - 1) // pages_per_copy
    students = load_students()
    return render_template(
        "split.html",
        active_page="split",
        total_items=total_items,
        pages_per_copy=pages_per_copy,
        students=students,
    )


import subprocess
import threading
import time

# ... existing imports ...


# --- Batch Processing State ---
class BatchJob:
    """Expose cooperative background processing through a polling-friendly state."""

    def __init__(self):
        """Initialize the state shared by one background batch job."""
        self.running = False
        self.stop_requested = False
        self.logs = []
        self.progress = 0
        self.total = 100
        self.current_action = "Inactif"
        self._inference_started_at = None
        self._inference_elapsed_seconds = None

    def inference_started(self):
        self._inference_started_at = time.time()
        self._inference_elapsed_seconds = 0

    def inference_finished(self):
        if self._inference_started_at is not None:
            self._inference_elapsed_seconds = max(0, int(time.time() - self._inference_started_at))
            self._inference_started_at = None

    @property
    def inference_elapsed_seconds(self):
        if self._inference_started_at is not None:
            return max(0, int(time.time() - self._inference_started_at))
        return self._inference_elapsed_seconds

    def start_batch_function(self, job_type, start, end, project_dir, force=False):
        """Start an extraction or correction job in a background thread."""
        # The HTTP request only starts the worker.  Progress and logs are kept
        # on this object because the browser polls them through /api/batch/status.
        if self.running:
            return False
        self.running = True
        self.stop_requested = False
        self.logs = []
        self.progress = 0
        self.current_action = f"Démarrage: {job_type} [{start}-{end}]"
        self._inference_started_at = None
        self._inference_elapsed_seconds = None

        def run_wrapper():
            """Execute the selected batch while capturing output for polling."""
            # Batch code predates this web layer and writes to stdout/logging;
            # temporarily redirect both streams so the existing UI can display
            # one consistent activity feed.
            # Sauvegarder le handler original du logger korektor
            import logging

            korektor_logger = logging.getLogger("korektor")
            original_handler = korektor_logger.handlers[0] if korektor_logger.handlers else None
            failed = False

            try:
                # Capture standard output/error to self.logs
                class LogCapture:
                    def __init__(self, job):
                        """Bind stdout capture to the active batch job."""
                        self.job = job

                    def write(self, text):
                        """Append stdout fragments while preserving line breaks."""
                        if text:
                            # print() may write the message and its newline separately.
                            # Keep both writes so the frontend can reconstruct lines.
                            self.job.logs.append(text)
                            # Heuristic progress update from logs
                            if "Processing" in text or "Traitem" in text:
                                # Don't overwrite calculated progress if it's running
                                pass

                            # Keep action updated
                            if text.strip() and not text.startswith("==="):
                                self.job.current_action = text.strip()[:50]  # Limit length

                    def flush(self):
                        """Provide the file-like flush operation expected by print()."""
                        pass

                original_stdout = sys.stdout
                sys.stdout = LogCapture(self)

                # Créer un handler personnalisé pour capturer les logs du logger
                class LogCaptureHandler(logging.StreamHandler):
                    def __init__(self, job):
                        """Initialize a logging handler bound to a batch job."""
                        super().__init__(sys.stdout)  # sys.stdout est déjà LogCapture
                        self.job = job

                    def emit(self, record):
                        """Append one formatted logging record to the job buffer."""
                        msg = self.format(record)
                        # Ajouter directement aux logs sans passer par sys.stdout
                        if hasattr(self.job, "logs"):
                            # The frontend concatenates log entries, so preserve the
                            # line boundary normally added by StreamHandler.emit().
                            self.job.logs.append(msg if msg.endswith("\n") else msg + "\n")
                            # Mettre à jour l'action en cours
                            if msg.strip() and not msg.startswith("[") and len(msg) < 50:
                                self.job.current_action = msg.strip()

                # Remplacer le handler par le nôtre
                korektor_logger.handlers = []
                korektor_logger.addHandler(LogCaptureHandler(self))

                print(f"Starting {job_type}...")

                try:
                    total = end - start + 1
                    count = 0

                    # Define stop callback for deep abort
                    check_stop = lambda: self.stop_requested

                    if job_type == "extraction":
                        for i in range(start, end + 1):
                            if self.stop_requested:
                                print(">>> Interruption demandée par l'utilisateur.")
                                break

                            print(f"Processing Copy {i}/{end}...")
                            # Update progress BEFORE work starts on item
                            self.current_action = f"Extraction Copie {i}"

                            batch_extraction.extract_copy(
                                i,
                                project_dir=project_dir,
                                stop_callback=check_stop,
                                force=force,
                                inference_callback=lambda active: (
                                    self.inference_started() if active else self.inference_finished()
                                ),
                            )

                            count += 1
                            self.progress = int((count / total) * 100)

                    elif job_type == "correction":
                        # Correction depends on a complete local context: the
                        # schema, extracted answers, subject, answer key, and
                        # rubric must all be available before the LLM is called.
                        # Obtenir la base de données (init_db est appelé uniquement si tables n'existent pas)
                        db = get_db(project_dir)
                        # Vérifier si les tables existent déjà pour éviter init inutile
                        from database import init_db

                        table_check = db.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name='corrections'"
                        ).fetchone()
                        if not table_check:
                            print(f"Initialisation de la base de données pour {project_dir}...")
                            init_db(db)
                        else:
                            print(f"Base de données déjà initialisée pour {project_dir}")

                        # Vérifier et charger les données manquantes
                        from database import load_all_extractions

                        # Charger extractions (déjà protégé : ne charge pas si extraction_verified=1)
                        load_all_extractions(db, project_dir)

                        # Charger sujet/corrige/bareme UNIQUEMENT s'ils n'existent pas dans la base
                        from database import load_bareme, load_corrige, load_sujet

                        sujet_exists = db.execute("SELECT COUNT(*) FROM sujet").fetchone()[0] > 0
                        corrige_exists = (
                            db.execute("SELECT COUNT(*) FROM corrige").fetchone()[0] > 0
                        )
                        bareme_exists = db.execute("SELECT COUNT(*) FROM bareme").fetchone()[0] > 0

                        sujet_path = os.path.join(project_dir, "sujet.md")
                        corrige_path = os.path.join(project_dir, "corrige.md")
                        bareme_path = os.path.join(project_dir, "bareme.csv")

                        if os.path.exists(sujet_path) and not sujet_exists:
                            print("Chargement de sujet.md...")
                            load_sujet(db, sujet_path)
                            sujet_exists = True

                        if os.path.exists(corrige_path) and not corrige_exists:
                            print("Chargement de corrige.md...")
                            load_corrige(db, corrige_path)
                            corrige_exists = True

                        if os.path.exists(bareme_path) and not bareme_exists:
                            print("Chargement de bareme.csv...")
                            sujet_content = ""
                            if os.path.exists(sujet_path):
                                with open(sujet_path, "r", encoding="utf-8") as f:
                                    sujet_content = f.read()
                            load_bareme(db, bareme_path, sujet_content)
                            bareme_exists = True

                        # Recalculer les flags apres chargement conditionnel
                        sujet_exists = db.execute("SELECT COUNT(*) FROM sujet").fetchone()[0] > 0
                        corrige_exists = (
                            db.execute("SELECT COUNT(*) FROM corrige").fetchone()[0] > 0
                        )
                        bareme_exists = db.execute("SELECT COUNT(*) FROM bareme").fetchone()[0] > 0

                        if sujet_exists and corrige_exists and bareme_exists:
                            print("Sujet, corrigé et barème déjà chargés dans la base")

                            # Obtenir le provider
                            from llm_providers import get_provider

                            provider = get_provider()

                            for i in range(start, end + 1):
                                if self.stop_requested:
                                    print(">>> Interruption demandée par l'utilisateur.")
                                    break

                                # Vérifier si la copie a déjà DES corrections (même partielles)
                                existing_count = db.execute(
                                    "SELECT COUNT(*) FROM corrections WHERE copie_id = ?",
                                    [i],
                                ).fetchone()[0]

                                if existing_count > 0 and not force:
                                    print(
                                        f"⏭️  Copie {i} a déjà {existing_count}/26 corrections, passage à la suivante (utilise force=True pour écraser)"
                                    )
                                    count += 1
                                    self.progress = int((count / total) * 100)
                                    continue

                                print(f"Correcting Copy {i}/{end}...")
                                self.current_action = f"Correction Copie {i}"

                                # Si force=True, supprimer les corrections existantes de cette copie
                                if force and existing_count > 0:
                                    db.execute(
                                        "DELETE FROM corrections WHERE copie_id = ?",
                                        [i],
                                    )
                                    db.commit()
                                    print(
                                        f"✏️  Suppression des {existing_count} corrections existantes pour la copie {i}"
                                    )

                                # Appeler la correction incrémentale v2
                                batch_correction_v2.correct_copy_incremental(
                                    copy_id=i,
                                    provider=provider,
                                    db=db,
                                    project_dir=project_dir,
                                    test_mode=False,
                                    inference_callback=lambda active: (
                                        self.inference_started() if active else self.inference_finished()
                                    ),
                                )

                                count += 1
                                self.progress = int((count / total) * 100)

                            db.close()
                except Exception as e:
                    failed = True
                    print(f"Error in batch loop: {e}")
                    import traceback

                    traceback.print_exc(file=sys.stdout)

                if self.stop_requested:
                    self.current_action = "Arrêté"
                    self.logs.append("=== Traitement interrompu ===\n")
                elif failed:
                    self.current_action = "Erreur"
                    self.logs.append("=== Échec du traitement ===\n")
                else:
                    self.progress = 100
                    self.current_action = "Terminé"
                    self.logs.append("=== Fin du traitement ===\n")

            except Exception as e:
                self.logs.append(f"Erreur fatale wrapper: {e}\n")
                self.current_action = "Erreur"
            finally:
                # Restaurer le handler original du logger
                sys.stdout = original_stdout
                if korektor_logger and original_handler:
                    korektor_logger.handlers = [original_handler]
                self.running = False

        thread = threading.Thread(target=run_wrapper)
        thread.start()
        return True

    def start(self, command):
        """Retain the legacy start API; batch starts use start_batch_function."""
        pass

    def stop(self):
        """Request cooperative cancellation of the active background job."""
        if self.running:
            self.stop_requested = True
            self.logs.append("=== Demande d'arrêt reçue... ===\n")
            return True
        return False


current_job = BatchJob()


@app.route("/process")
def process_page():
    """Render the combined batch processing page."""
    return render_template("process.html", active_page="process")


@app.route("/api/batch/start", methods=["POST"])
def batch_start():
    """Validate and start a requested extraction or correction batch."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Payload JSON invalide."}), 400
    job_type = data.get("type")  # 'extraction' or 'correction'
    req_start = data.get("start")
    req_end = data.get("end")

    # Determine a range from the copies that actually exist.  This keeps the
    # endpoint independent from assumptions about contiguous copy numbering.
    existing = get_copy_ids()  # helper needed
    if not existing:
        return jsonify({"success": False, "error": "Aucune copie trouvée"}), 400

    try:
        start_id = int(req_start) if req_start is not None else min(existing)
        end_id = int(req_end) if req_end is not None else max(existing)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Les bornes doivent être des entiers."}), 400

    if start_id < 1 or end_id > max(existing) or start_id > end_id:
        return (
            jsonify(
                {
                    "success": False,
                    "error": f"Plage invalide : choisissez une plage entre 1 et {max(existing)}.",
                }
            ),
            400,
        )

    # Launch Job
    if current_job.start_batch_function(
        job_type, start_id, end_id, config.project_dir, force=data.get("force", False)
    ):
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Un job est déjà en cours"}), 409


@app.route("/api/batch/stop", methods=["POST"])
def batch_stop():
    """Request cancellation of the current batch job."""
    if current_job.stop():
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Aucun job actif"}), 400


@app.route("/api/batch/status")
def batch_status():
    """Return current batch state and recent log entries as JSON."""
    return jsonify(
        {
            "running": current_job.running,
            "progress": current_job.progress,
            "action": current_job.current_action,
            "inference_elapsed_seconds": current_job.inference_elapsed_seconds,
            "logs": current_job.logs[-50:],  # Send last 50 lines
        }
    )


def get_copy_ids():
    """Return sorted copy numbers discovered in the output directory."""
    ids = []
    output_dir = config.get_output_dir()
    if os.path.exists(output_dir):
        for f in os.listdir(output_dir):
            if f.startswith("Copie_") and f.endswith(".pdf"):
                try:
                    ids.append(int(f.replace("Copie_", "").replace(".pdf", "")))
                except:
                    pass
    return sorted(ids)


@app.route("/verify")
def verify_page():
    """Render the correction validation page."""
    return render_template("verify.html", active_page="verify")


@app.route("/rapports")
def rapports_page():
    """Render the per-student Markdown/PDF feedback report page."""
    return render_template("rapports.html", active_page="rapports")


# --- ROUTES: API Import ---


@app.route("/api/import", methods=["POST"])
def api_import():
    """Accept an uploaded PDF and create the PDF artifact used by the splitter."""
    try:
        file = request.files.get("file")
        already_a4 = request.form.get("already_a4") == "true"
        sort_booklet = request.form.get("sort_booklet") == "true"

        try:
            pages_per_copy = int(request.form.get("pages_per_copy", 2 if already_a4 else 4))
        except (TypeError, ValueError):
            return (
                jsonify(
                    {"success": False, "error": "Le nombre de pages par copie doit être un entier"}
                ),
                400,
            )
        if pages_per_copy < 1:
            return (
                jsonify(
                    {"success": False, "error": "Le nombre de pages par copie doit être positif"}
                ),
                400,
            )

        if not file:
            return jsonify({"success": False, "error": "No file uploaded"}), 400

        # Save temp file to disk to avoid 'cannot pickle' error with pypdf deepcopy on stream
        temp_path = config.get_path("temp_import.pdf")
        file.save(temp_path)

        try:
            if already_a4:
                shutil.copyfile(temp_path, config.get_sorted_pdf_path())
            else:
                process_a3_split(temp_path, config.get_sorted_pdf_path(), sort_booklet)
            config.set_pages_per_copy(pages_per_copy)
        finally:
            # Cleanup
            if os.path.exists(temp_path):
                os.remove(temp_path)

        # Reset doc handle
        global splitter_doc
        if splitter_doc:
            splitter_doc.close()
        splitter_doc = None

        return jsonify({"success": True})
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# --- ROUTES: API Splitter ---


@app.route("/api/split/thumbnail/<int:page_num>")
def split_thumbnail(page_num):
    """Render a thumbnail of one page in the split-view PDF."""
    try:
        doc = get_splitter_doc()
        if not doc:
            return "", 404

        page_idx = page_num - 1
        if 0 <= page_idx < len(doc):
            page = doc[page_idx]
            rect = page.rect
            try:
                header_ratio = float(request.args.get("header_ratio", "0.15"))
            except (TypeError, ValueError):
                header_ratio = 0.15
            header_ratio = max(0.0, min(0.5, header_ratio))

            pix = page.get_pixmap(dpi=72)
            if header_ratio > 0:
                line_y = round(pix.height * header_ratio)
                for y in range(max(0, line_y - 1), min(pix.height, line_y + 2)):
                    for x in range(pix.width):
                        pix.set_pixel(x, y, (255, 0, 0))
            img_data = pix.tobytes("png")
            return send_file(io.BytesIO(img_data), mimetype="image/png")
        return "Page not found", 404
    except Exception as e:
        return str(e), 500


@app.route("/api/split/process", methods=["POST"])
def split_process():
    """Create anonymized copy PDFs from submitted page assignments."""
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "No data"}), 400

    try:
        if not isinstance(data, list):
            return jsonify({"success": False, "error": "Format de données invalide"}), 400

        header_ratio = data[0].get("header_ratio", 0.15) if data else 0.15
        try:
            header_ratio = float(header_ratio)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Le pourcentage doit être numérique"}), 400
        if not 0 <= header_ratio <= 0.5:
            return (
                jsonify(
                    {"success": False, "error": "Le pourcentage doit être compris entre 0 et 50 %"}
                ),
                400,
            )

        doc = get_splitter_doc()
        if not doc:
            return jsonify({"success": False, "error": "Doc not loaded"}), 500
        pages_per_copy = config.get_pages_per_copy()

        # Convert the flat browser selection into contiguous segments.  A
        # continuation or repeated student ID extends the current copy; any
        # other selection starts a new copy.
        copy_segments = []
        current_copy_segments = []
        current_student_id = None
        copy_students = []

        for item in data:
            page_num = item["page_num"]
            is_continuation = item.get("is_continuation", False)
            student_id = item.get("student_id", None)
            start_idx = page_num - 1

            is_same_student = student_id is not None and student_id == current_student_id

            if is_continuation or is_same_student:
                current_copy_segments.append(start_idx)
            else:
                if current_copy_segments:
                    copy_segments.append(current_copy_segments)
                    copy_students.append(current_student_id)
                current_copy_segments = [start_idx]
                current_student_id = student_id

        if current_copy_segments:
            copy_segments.append(current_copy_segments)
            copy_students.append(current_student_id)

        # Each generated PDF is also registered in correspondance.csv, which is
        # the file-based bridge between anonymous copy numbers and students.

        output_dir = config.get_output_dir()
        csv_path = config.get_path("correspondance.csv")
        students_list = load_students()
        student_info_map = {s["id"]: s for s in students_list}

        with open(csv_path, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(
                [
                    "Copie_ID",
                    "Fichier",
                    "Nb_Pages",
                    "Page_Range",
                    "Etudiant_ID",
                    "Nom",
                    "Prenom",
                ]
            )

            for i, segments in enumerate(copy_segments):
                copy_id = i + 1
                s_id = copy_students[i] if i < len(copy_students) else ""

                s_nom = ""
                s_prenom = ""
                if s_id and s_id in student_info_map:
                    s_nom = student_info_map[s_id].get("name", "")
                    s_prenom = student_info_map[s_id].get("firstname", "")

                new_doc = pymupdf.open()
                total_pages = 0

                # Range
                if segments:
                    min_p = segments[0] + 1
                    last_start = segments[-1]
                    max_p = min(last_start + pages_per_copy, len(doc))
                    page_range_str = f"{min_p}--{max_p}"
                else:
                    page_range_str = ""

                for start_idx in segments:
                    end_idx = min(start_idx + pages_per_copy, len(doc))
                    new_doc.insert_pdf(doc, from_page=start_idx, to_page=end_idx - 1)

                    # Header mask & Numbering
                    segment_first_page = new_doc[total_pages]
                    rect = segment_first_page.rect
                    header_rect = pymupdf.Rect(0, 0, rect.width, rect.height * header_ratio)

                    shape = segment_first_page.new_shape()
                    shape.draw_rect(header_rect)
                    shape.finish(color=(1, 1, 1), fill=(1, 1, 1))
                    shape.commit()

                    text = f"Copie {copy_id}"
                    segment_first_page.insert_textbox(
                        header_rect, text, fontsize=40, color=(0, 0, 0), align=1
                    )

                    total_pages += end_idx - start_idx

                filename = f"Copie_{copy_id:02d}.pdf"
                filepath = os.path.join(output_dir, filename)
                new_doc.save(filepath)
                new_doc.close()

                writer.writerow(
                    [
                        copy_id,
                        filename,
                        total_pages,
                        page_range_str,
                        s_id or "",
                        s_nom,
                        s_prenom,
                    ]
                )

        return jsonify({"success": True, "message": f"{len(copy_segments)} copies générées."})

    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# --- ROUTES: API Verification/Validator ---


@app.route("/api/copies")
def list_copies():
    """List copy identifiers found in the file-based output directory."""
    copies = []
    output_dir = config.get_output_dir()
    if os.path.exists(output_dir):
        files = os.listdir(output_dir)
        # Filter for Copie_XX.pdf ? No, validator logic used dirs or matching strings
        # Adjust logic: We generated Copie_XX.pdf files in root of OUTPUT_DIR
        # But validator expects folders?
        # WAIT: split_copies.py generated .pdf files.
        # But validator_app expected what?
        # Let's check validator expectations from extraction.
        # Implementation Plan says: "Reads/Writes directly to copies_anonymes_validees/"
        # But batch_extraction generates markdown WHERE?
        # If we use simple PDF files, we need corresponding markdown files.
        # Let's assume we create a Folder Structure: copies_anonymes_validees/Copie_XX/
        # Or put the PDF INSIDE the folder?
        # Let's check batch scripts later. For now, let's list PDF files based on naming convention.

        # Quick fix: scan for Copy_*.pdf and sort numerically
        pdf_files = [f for f in files if f.startswith("Copie_") and f.endswith(".pdf")]
        pdf_files.sort(key=lambda x: int(x.split("_")[1].split(".")[0]))
        return jsonify([f.replace(".pdf", "") for f in pdf_files])

    return jsonify(copies)


@app.route("/api/bareme")
def get_bareme():
    """Return the file-based rubric CSV as JSON rows."""
    questions = []
    path = config.get_path("bareme.csv")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    questions.append(row)
        except Exception as e:
            print(f"Error reading bareme: {e}")
    return jsonify(questions)


@app.route("/api/pdf/<copie_id>")
def get_pdf(copie_id):
    """Serve the PDF associated with a copy identifier."""
    # copie_id like "Copie_01"
    filename = f"{copie_id}.pdf"
    path = os.path.join(config.get_output_dir(), filename)
    if os.path.exists(path):
        return send_file(path)
    return "Not found", 404


@app.route("/api/copies/<int:copie_id>/pdf")
def api_student_pdf(copie_id):
    """Generate or serve a student's Typst report inline."""
    db_path = Path(config.get_path("korektor.db")).resolve()
    if not db_path.is_file():
        return jsonify({"error": "Base de données introuvable pour ce projet"}), 404

    try:
        with closing(get_db(config.project_dir)) as db:
            copy = db.execute("SELECT 1 FROM copies WHERE id = ?", (copie_id,)).fetchone()
        if copy is None:
            return jsonify({"error": f"Copie {copie_id} introuvable"}), 404

        pdf_path = Path(config.get_path("exports_pdf", f"Copie_{copie_id:02d}.pdf")).resolve()
        if request.args.get("refresh") == "1" or not pdf_path.is_file():
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            generate_student_pdf(copie_id, str(db_path), str(pdf_path))
        return send_file(pdf_path, mimetype="application/pdf", as_attachment=False)
    except (OSError, RuntimeError, sqlite3.Error) as error:
        app.logger.exception("Impossible de générer le PDF de la copie %s", copie_id)
        return jsonify({"error": f"Génération du PDF impossible : {error}"}), 500


@app.route("/api/copies/generate-all-pdfs", methods=["POST"])
def api_generate_all_student_pdfs():
    """Regenerate the current promotion's Typst reports in exports_pdf."""
    db_path = Path(config.get_path("korektor.db")).resolve()
    if not db_path.is_file():
        return jsonify({"error": "Base de données introuvable pour ce projet"}), 404

    try:
        with closing(get_db(config.project_dir)) as db:
            copy_ids = [row[0] for row in db.execute("SELECT id FROM copies ORDER BY id")]
    except (OSError, sqlite3.Error) as error:
        app.logger.exception("Impossible de lister les copies du projet")
        return jsonify({"error": f"Lecture des copies impossible : {error}"}), 500

    output_dir = Path(config.get_path("exports_pdf")).resolve()
    try:
        if copy_ids:
            output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        app.logger.exception("Impossible de créer le dossier des PDF")
        return jsonify({"error": f"Création du dossier des PDF impossible : {error}"}), 500

    generated = 0
    errors = []
    for copy_id in copy_ids:
        try:
            generate_student_pdf(
                copy_id, str(db_path), str(output_dir / f"Copie_{copy_id:02d}.pdf")
            )
            generated += 1
        except (OSError, RuntimeError, sqlite3.Error) as error:
            app.logger.exception("Échec du PDF de la copie %s", copy_id)
            errors.append({"copie_id": copy_id, "error": str(error)})

    result = {
        "success": not errors,
        "generated": generated,
        "total": len(copy_ids),
        "errors": errors,
        "message": f"{generated} PDF générés, {len(errors)} échec(s)",
    }
    return jsonify(result), 500 if errors else 200


@app.route("/api/corrections/<int:correction_id>/crop")
def api_correction_crop(correction_id):
    """Serve an annotated PNG only when it belongs to the active project."""
    db_path = Path(config.get_path("korektor.db")).resolve()
    if not db_path.is_file():
        return jsonify({"error": "Base de données introuvable pour ce projet"}), 404

    try:
        with closing(get_db(config.project_dir)) as db:
            row = db.execute(
                "SELECT crop_image_path FROM corrections WHERE id = ?", (correction_id,)
            ).fetchone()
        if row is None:
            return jsonify({"error": f"Correction {correction_id} introuvable"}), 404
        if not isinstance(row["crop_image_path"], str) or not row["crop_image_path"].strip():
            return jsonify({"error": "Aucun extrait annoté pour cette correction"}), 404

        project_dir = Path(config.project_dir).resolve()
        crop = (project_dir / row["crop_image_path"]).resolve()
        if (
            not crop.is_relative_to(project_dir)
            or crop.suffix.lower() != ".png"
            or not crop.is_file()
        ):
            return jsonify({"error": "Extrait PNG introuvable pour cette correction"}), 404
        return send_file(crop, mimetype="image/png", as_attachment=False)
    except (OSError, sqlite3.Error) as error:
        app.logger.exception("Impossible de servir l'extrait de la correction %s", correction_id)
        return jsonify({"error": f"Lecture de l'extrait impossible : {error}"}), 500


# Use subfolders for data to keep clean
def get_copy_dir(copie_id):
    """Return and create the file directory for one copy."""
    # Legacy file-based endpoints use this directory, while newer correction
    # and reporting routes use SQLite.  Keeping the adapter is important for
    # old projects and for the validation pages.
    d = os.path.join(config.get_output_dir(), copie_id)
    if not os.path.exists(d):
        os.makedirs(d)
    return d


@app.route("/api/extraction/<copie_id>", methods=["GET", "POST"])
def handle_extraction(copie_id):
    """Read or write the legacy Markdown extraction for one copy."""
    # Store markdown in a subfolder: copies_anonymes_validees/Copie_XX/extraction.md
    copy_dir = get_copy_dir(copie_id)
    file_path = os.path.join(copy_dir, "extraction.md")

    if request.method == "POST":
        content = request.json.get("content", "")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return jsonify({"success": True})
    else:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                return jsonify({"content": f.read()})
        return jsonify({"content": ""})


@app.route("/api/correction/<copie_id>", methods=["GET", "POST"])
def handle_correction(copie_id):
    """Read or write the legacy CSV correction for one copy."""
    # Store CSV in a subfolder: copies_anonymes_validees/Copie_XX/correction.csv
    copy_dir = get_copy_dir(copie_id)
    ai_file_path = os.path.join(copy_dir, "correction.csv")
    prof_file_path = os.path.join(copy_dir, "correction_prof.csv")

    if request.method == "POST":
        data = request.json
        if data:
            # Fix: Load bareme to get strict field order
            try:
                fieldnames = []
                bareme_path = config.get_path("bareme.csv")
                if os.path.exists(bareme_path):
                    with open(bareme_path, "r", encoding="utf-8") as bf:
                        reader = csv.DictReader(bf)
                        fieldnames = reader.fieldnames

                if not fieldnames:
                    # Fallback if bareme not found/empty
                    fieldnames = list(data[0].keys())

                # Compulsory write to correction_prof.csv
                with open(prof_file_path, "w", newline="", encoding="utf-8") as f:
                    # extrasaction='ignore' prevents errors if frontend sent extra props
                    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(data)
            except Exception as e:
                print(f"Error saving correction: {e}")
                return jsonify({"success": False, "error": str(e)}), 500

        return jsonify({"success": True})
    else:
        rows = []
        # Priority read: Prof file first, then AI file
        target_path = prof_file_path if os.path.exists(prof_file_path) else ai_file_path

        if os.path.exists(target_path):
            with open(target_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        return jsonify(rows)


# --- ROUTES: DB Verification (SQLite) ---


@app.route("/api/db/bareme")
def get_db_bareme():
    """Return the SQLite rubric rows as JSON."""
    """Recupere le bareme depuis la base de donnees SQLite"""
    db = None
    try:
        db = get_db(config.project_dir)
        bareme = db_get_bareme(db)
        return jsonify([dict(row) for row in bareme])
    except Exception as e:
        print(f"Error reading bareme from DB: {e}")
        return get_bareme()
    finally:
        if db is not None:
            db.close()


@app.route("/api/db/copies")
def list_db_copies():
    """Return copy identifiers and student metadata from SQLite."""
    """Liste toutes les copies depuis la base de donnees SQLite"""
    db = None
    try:
        db = get_db(config.project_dir)
        copies = db.execute(
            "SELECT id, etudiant_id, nom, prenom FROM copies ORDER BY id"
        ).fetchall()
        return jsonify(
            [
                {
                    "id": row["id"],
                    "etudiant_id": row["etudiant_id"],
                    "nom": row["nom"],
                    "prenom": row["prenom"],
                }
                for row in copies
            ]
        )
    except Exception as e:
        print(f"Error listing copies from DB: {e}")
        return jsonify([])
    finally:
        if db is not None:
            db.close()


@app.route("/api/db/corrections/<int:copie_id>", methods=["GET", "POST"])
def get_db_corrections(copie_id):
    """Read or update teacher-visible corrections for one copy in SQLite."""
    """Recupere toutes les corrections d'une copie depuis la base de donnees SQLite"""
    db = None
    try:
        db = get_db(config.project_dir)

        if request.method == "POST":
            corrections = request.get_json(silent=True)
            if not isinstance(corrections, list):
                return (
                    jsonify({"success": False, "error": "Format de corrections invalide"}),
                    400,
                )

            for correction in corrections:
                question = correction.get("question")
                if not question:
                    continue

                bareme_row = db.execute(
                    "SELECT id FROM bareme WHERE question = ?", [question]
                ).fetchone()
                if not bareme_row:
                    continue

                db.execute(
                    """
                    UPDATE corrections
                    SET reponse_etudiant = ?,
                        note_prof = ?,
                        commentaire_prof = ?,
                        validee_par_prof = 1,
                        date_validation = CURRENT_TIMESTAMP
                    WHERE copie_id = ? AND bareme_id = ?
                """,
                    [
                        correction.get("reponses etudiant", ""),
                        correction.get("note"),
                        correction.get("commentaires eventuels du correcteur", ""),
                        copie_id,
                        bareme_row["id"],
                    ],
                )

            db.commit()
            return jsonify({"success": True})

        # The prof-approved value wins over the LLM value.  This rule is used
        # consistently by validation, reports, exports, and dashboard stats.
        corrections = db.execute(
            """
            SELECT
                c.id,
                c.bareme_id,
                b.question,
                b.texte_question,
                b.type_qcm,
                b.reponse_attendue,
                b.points,
                c.reponse_etudiant,
                CASE WHEN c.validee_par_prof THEN c.note_prof ELSE c.note_llm END as note,
                CASE WHEN c.validee_par_prof THEN c.commentaire_prof ELSE c.commentaire_llm END as commentaire,
                c.raisonnement_llm as raisonnement,
                c.nb_bonnes_llm,
                c.nb_mauvaises_llm,
                c.bonus_redaction_llm,
                c.validee_par_prof,
                c.crop_image_path,
                c.bounding_boxes
            FROM corrections c
            JOIN bareme b ON c.bareme_id = b.id
            WHERE c.copie_id = ?
            ORDER BY b.id
        """,
            [copie_id],
        ).fetchall()

        result = []
        for row in corrections:
            try:
                bounding_boxes = json.loads(row["bounding_boxes"] or "[]")
                if not isinstance(bounding_boxes, list):
                    bounding_boxes = []
            except (json.JSONDecodeError, TypeError):
                bounding_boxes = []
            result.append(
                {
                    "id": row["id"],
                    "question": row["question"],
                    "texte_question": row["texte_question"],
                    "qcm (oui/non)": "oui" if row["type_qcm"] else "non",
                    "reponse attendue": row["reponse_attendue"],
                    "points": row["points"],
                    "reponses etudiant": row["reponse_etudiant"],
                    "note": row["note"],
                    "commentaires eventuels du correcteur": row["commentaire"],
                    "raisonnement_llm": row["raisonnement"],
                    "nbre de bonnes reponses si QCM": row["nb_bonnes_llm"],
                    "nbre de mauvaises reponses si QCM": row["nb_mauvaises_llm"],
                    "Redaction si QCM": row["bonus_redaction_llm"],
                    "validee_par_prof": row["validee_par_prof"],
                    "crop_image_path": row["crop_image_path"],
                    "bounding_boxes": bounding_boxes,
                }
            )
        return jsonify(result)
    except Exception as e:
        print(f"Error reading corrections from DB for copy {copie_id}: {e}")
        import traceback

        traceback.print_exc()
        return jsonify([])
    finally:
        if db is not None:
            db.close()


# --- ROUTES: Rapports PDF (feedback étudiant) ---


def _load_copie_and_corrections(db, copie_id):
    """Return (copie_info dict, corrections list) for one copy, or (None, []) if missing."""
    copie_row = db.execute(
        "SELECT id, etudiant_id, nom, prenom FROM copies WHERE id = ?", [copie_id]
    ).fetchone()
    if not copie_row:
        return None, []

    corrections = db.execute(
        """
        SELECT
            b.question,
            b.points,
            c.reponse_etudiant,
            CASE WHEN c.validee_par_prof THEN c.note_prof ELSE c.note_llm END as note,
            CASE WHEN c.validee_par_prof THEN c.commentaire_prof ELSE c.commentaire_llm END as commentaire
        FROM corrections c
        JOIN bareme b ON c.bareme_id = b.id
        WHERE c.copie_id = ?
        ORDER BY b.id
    """,
        [copie_id],
    ).fetchall()

    result = [
        {
            "question": row["question"],
            "points": row["points"],
            "note": row["note"],
            "reponses etudiant": row["reponse_etudiant"],
            "commentaires eventuels du correcteur": row["commentaire"],
        }
        for row in corrections
    ]
    return dict(copie_row), result


def _report_paths(copie_info):
    """Return (md_path, pdf_path) for the report of one student, creating the dir."""
    reports_dir = report_builder.get_reports_dir(config.project_dir)
    basename = report_builder.report_basename(
        copie_info["id"],
        copie_info.get("etudiant_id"),
        copie_info.get("nom"),
        copie_info.get("prenom"),
    )
    return (
        os.path.join(reports_dir, f"{basename}.md"),
        os.path.join(reports_dir, f"{basename}.pdf"),
    )


def _corrections_total(corrections):
    """Sum the 'note' field of a list of correction dicts, ignoring missing/invalid values."""
    total = 0.0
    for row in corrections:
        try:
            total += float(row.get("note") or 0)
        except (TypeError, ValueError):
            pass
    return total


def _get_class_totals(db):
    """Return (list of per-copy totals across the class, max_points from the barème)."""
    rows = db.execute("""
        SELECT SUM(CASE WHEN validee_par_prof THEN note_prof ELSE note_llm END) as total
        FROM corrections
        GROUP BY copie_id
        """).fetchall()
    max_points = db.execute("SELECT COALESCE(SUM(points), 0) FROM bareme").fetchone()[0] or 0
    totals = [row["total"] for row in rows if row["total"] is not None]
    return totals, max_points


def _get_question_class_stats(db):
    """Return {question: {points, min, max, moyenne}} across the class, from the bareme order."""
    rows = db.execute("""
        SELECT b.id, b.question, b.points,
               CASE WHEN c.validee_par_prof THEN c.note_prof ELSE c.note_llm END AS note
        FROM bareme b
        LEFT JOIN corrections c ON c.bareme_id = b.id
        ORDER BY b.id
        """).fetchall()

    by_id = {}
    order = []
    for row in rows:
        qid = row["id"]
        if qid not in by_id:
            by_id[qid] = {"question": row["question"], "points": row["points"], "notes": []}
            order.append(qid)
        if row["note"] is not None:
            by_id[qid]["notes"].append(row["note"])

    stats = {}
    for qid in order:
        entry = by_id[qid]
        notes = entry["notes"]
        stats[entry["question"]] = {
            "points": entry["points"],
            "min": min(notes) if notes else None,
            "max": max(notes) if notes else None,
            "moyenne": (sum(notes) / len(notes)) if notes else None,
        }
    return stats


def _build_report_content(db, copie_info, corrections, md_path):
    """Build the report Markdown, generating the class-comparison charts PNGs alongside it."""
    # Reports are generated from final correction values and current class
    # statistics, so a teacher edit is reflected in both the prose and charts.
    sujet_title, sujet_date = report_builder.parse_sujet_header(db_get_sujet(db))
    # The final LLM call is made after all per-question feedback is available,
    # allowing it to synthesize the complete copy rather than isolated answers.
    provider = get_provider()

    charts = []

    class_totals, max_points = _get_class_totals(db)
    if len(class_totals) >= 1:  # FIXME: bring this back to 2
        student_total = _corrections_total(corrections)
        candidate_name = f"graphique_{os.path.splitext(os.path.basename(md_path))[0]}.png"
        candidate_path = os.path.join(os.path.dirname(md_path), candidate_name)
        if report_builder.generate_grade_chart(
            student_total, class_totals, max_points, candidate_path
        ):
            charts.append(
                {
                    "path": candidate_path,
                    "legend": "Répartition des notes de la classe, et de votre note",
                }
            )

    if corrections:
        question_stats = _get_question_class_stats(db)
        profile_rows = [
            {
                "question": row.get("question"),
                "points": row.get("points"),
                "note": row.get("note"),
                **{
                    k: v
                    for k, v in question_stats.get(row.get("question"), {}).items()
                    if k in ("min", "max", "moyenne")
                },
            }
            for row in corrections
        ]
        profile_name = f"profil_{os.path.splitext(os.path.basename(md_path))[0]}.png"
        profile_path = os.path.join(os.path.dirname(md_path), profile_name)
        if report_builder.generate_question_profile_chart(profile_rows, profile_path):
            charts.append(
                {"path": profile_path, "legend": "Profil par question, comparé à la classe"}
            )

    return report_builder.build_report_markdown(
        copie_info,
        corrections,
        sujet_title,
        sujet_date,
        charts=charts,
        nom_examen=config.project_name,
        provider=provider,
    )


@app.route("/api/rapports/<int:copie_id>", methods=["GET", "POST"])
def api_rapport_content(copie_id):
    """Read the saved report Markdown (generating it on the fly if absent), or save edits."""
    db = None
    try:
        db = get_db(config.project_dir)
        copie_info, corrections = _load_copie_and_corrections(db, copie_id)
        if copie_info is None:
            return jsonify({"error": "Copie introuvable"}), 404

        md_path, _ = _report_paths(copie_info)
        filename = os.path.basename(md_path)

        if request.method == "POST":
            content = (request.get_json(silent=True) or {}).get("content", "")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(content)
            return jsonify({"success": True, "filename": filename})

        if os.path.exists(md_path):
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
            exists = True
        else:
            content = _build_report_content(db, copie_info, corrections, md_path)
            exists = False

        return jsonify({"content": content, "filename": filename, "exists": exists})
    except Exception as e:
        print(f"Error handling report for copy {copie_id}: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if db is not None:
            db.close()


@app.route("/api/rapports/<int:copie_id>/generate", methods=["POST"])
def api_rapport_generate(copie_id):
    """Regenerate the report Markdown from the database, overwriting the saved file."""
    db = None
    try:
        db = get_db(config.project_dir)
        copie_info, corrections = _load_copie_and_corrections(db, copie_id)
        if copie_info is None:
            return jsonify({"error": "Copie introuvable"}), 404

        md_path, _ = _report_paths(copie_info)
        content = _build_report_content(db, copie_info, corrections, md_path)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)

        return jsonify({"content": content, "filename": os.path.basename(md_path)})
    except Exception as e:
        print(f"Error generating report for copy {copie_id}: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if db is not None:
            db.close()


@app.route("/api/rapports/<int:copie_id>/pdf", methods=["POST"])
def api_rapport_pdf(copie_id):
    """Compile the saved report Markdown to PDF and return it as a download."""
    db = None
    try:
        db = get_db(config.project_dir)
        copie_info, _ = _load_copie_and_corrections(db, copie_id)
        if copie_info is None:
            return jsonify({"error": "Copie introuvable"}), 404

        md_path, pdf_path = _report_paths(copie_info)
        if not os.path.exists(md_path):
            return jsonify({"error": "Le rapport Markdown doit d'abord être enregistré"}), 400

        report_builder.compile_markdown_to_pdf(md_path, pdf_path)
        return send_file(pdf_path, as_attachment=True, download_name=os.path.basename(pdf_path))
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        print(f"Error compiling report to PDF for copy {copie_id}: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if db is not None:
            db.close()


@app.route("/api/rapports/<int:copie_id>/file/<file_type>")
def api_rapport_file(copie_id, file_type):
    """Serve a previously generated report file (Markdown or PDF) inline, for viewing in a new tab."""
    if file_type not in ("md", "pdf"):
        return "Type de fichier invalide", 400

    db = None
    try:
        db = get_db(config.project_dir)
        copie_info, _ = _load_copie_and_corrections(db, copie_id)
        if copie_info is None:
            return "Copie introuvable", 404

        md_path, pdf_path = _report_paths(copie_info)
        path = md_path if file_type == "md" else pdf_path
        if not os.path.exists(path):
            return "Fichier introuvable", 404

        mimetype = "text/markdown; charset=utf-8" if file_type == "md" else "application/pdf"
        return send_file(path, mimetype=mimetype, as_attachment=False)
    finally:
        if db is not None:
            db.close()


class ReportBatchJob:
    """Background job regenerating every student's Markdown report then compiling it to PDF."""

    def __init__(self):
        """Initialize the idle state shared by the batch report job."""
        self.running = False
        self.stop_requested = False
        self.progress = 0
        self.total = 0
        self.current_action = "Inactif"
        self.results = []
        self.merged_pdf = None
        self.merge_error = None

    def start(self, project_dir):
        """Start the background thread iterating over every copy, unless already running."""
        # Report generation is intentionally independent from the correction
        # worker: teachers can regenerate deliverables without rerunning the
        # expensive OCR/LLM pipeline.
        if self.running:
            return False
        self.running = True
        self.stop_requested = False
        self.progress = 0
        self.total = 0
        self.results = []
        self.merged_pdf = None
        self.merge_error = None
        self.current_action = "Démarrage..."

        def run():
            """Regenerate and compile one report at a time, isolating failures per copy."""
            db = None
            try:
                db = get_db(project_dir)
                copies = db.execute("SELECT id FROM copies ORDER BY id").fetchall()
                self.total = len(copies)
                merged_pdf_paths = []

                for index, row in enumerate(copies, start=1):
                    if self.stop_requested:
                        self.current_action = "Arrêté"
                        break

                    copie_id = row["id"]
                    self.current_action = f"Copie {copie_id} ({index}/{self.total})"
                    entry = {
                        "copie_id": copie_id,
                        "filename": None,
                        "md_ok": False,
                        "pdf_ok": False,
                        "error": None,
                    }
                    try:
                        copie_info, corrections = _load_copie_and_corrections(db, copie_id)
                        if copie_info is None:
                            raise RuntimeError("Copie introuvable")

                        md_path, pdf_path = _report_paths(copie_info)
                        content = _build_report_content(db, copie_info, corrections, md_path)
                        with open(md_path, "w", encoding="utf-8") as f:
                            f.write(content)
                        entry["md_ok"] = True
                        entry["filename"] = os.path.basename(md_path)

                        try:
                            report_builder.compile_markdown_to_pdf(md_path, pdf_path)
                            entry["pdf_ok"] = True
                            merged_pdf_paths.append(pdf_path)
                        except Exception as pdf_exc:
                            entry["error"] = f"Échec PDF : {pdf_exc}"
                    except Exception as exc:
                        entry["error"] = str(exc)

                    self.results.append(entry)
                    self.progress = int(index / self.total * 100) if self.total else 100

                if not self.stop_requested and merged_pdf_paths:
                    self.current_action = "Fusion des PDF (pdftk)..."
                    merged_name = f"rapport_total_date-{datetime.now():%d-%m-%Y}.pdf"
                    merged_path = os.path.join(
                        report_builder.get_reports_dir(project_dir), merged_name
                    )
                    try:
                        report_builder.merge_pdfs(merged_pdf_paths, merged_path)
                        self.merged_pdf = merged_name
                    except RuntimeError as merge_exc:
                        self.merge_error = str(merge_exc)
            except Exception as exc:
                self.current_action = f"Erreur : {exc}"
            finally:
                if db is not None:
                    db.close()
                if not self.stop_requested:
                    self.progress = 100
                    self.current_action = "Terminé"
                self.running = False

        threading.Thread(target=run).start()
        return True

    def stop(self):
        """Request cooperative cancellation of the running batch."""
        if self.running:
            self.stop_requested = True
            return True
        return False


report_batch_job = ReportBatchJob()


@app.route("/api/rapports/batch/start", methods=["POST"])
def api_rapport_batch_start():
    """Start the background job regenerating and compiling every student's report."""
    if report_batch_job.start(config.project_dir):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Un batch de rapports est déjà en cours"}), 409


@app.route("/api/rapports/batch/stop", methods=["POST"])
def api_rapport_batch_stop():
    """Request cancellation of the running batch report job."""
    if report_batch_job.stop():
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Aucun batch actif"}), 400


@app.route("/api/rapports/batch/status")
def api_rapport_batch_status():
    """Return the batch report job's progress and per-copy results as JSON."""
    return jsonify(
        {
            "running": report_batch_job.running,
            "progress": report_batch_job.progress,
            "total": report_batch_job.total,
            "action": report_batch_job.current_action,
            "results": report_batch_job.results,
            "merged_pdf": report_batch_job.merged_pdf,
            "merge_error": report_batch_job.merge_error,
        }
    )


@app.route("/api/rapports/batch/merged")
def api_rapport_batch_merged():
    """Serve the all-in-one PDF produced by concatenating the last batch's reports (pdftk)."""
    if not report_batch_job.merged_pdf:
        return "Aucun PDF fusionné disponible", 404

    path = os.path.join(
        report_builder.get_reports_dir(config.project_dir), report_batch_job.merged_pdf
    )
    if not os.path.exists(path):
        return "Fichier introuvable", 404

    return send_file(
        path,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=report_batch_job.merged_pdf,
    )


# --- ROUTES: OCR Verification ---


@app.route("/verify_ocr")
def verify_ocr_page():
    """Render the OCR validation page."""
    return render_template("verify_ocr.html", active_page="verify_ocr")


def get_ocr_project_dir():
    """Return the active project directory used by OCR routes."""
    """Get the current project directory for OCR verification"""
    return config.project_dir


def get_ocr_copy_dir(copie_id):
    """Return and create the OCR directory for one copy."""
    """Get directory for a specific copy in the current project"""
    base_dir = os.path.join(get_ocr_project_dir(), "copies_anonymes_validees")
    d = os.path.join(base_dir, copie_id)
    if not os.path.exists(d):
        os.makedirs(d, exist_ok=True)
    return d


@app.route("/api/ocr/copies")
def list_ocr_copies():
    """List copy directories and PDFs available to the OCR validator."""
    """List all copies in the current project's copies_anonymes_validees"""
    copies_dir = os.path.join(get_ocr_project_dir(), "copies_anonymes_validees")
    copies = []

    if os.path.exists(copies_dir):
        for item in os.listdir(copies_dir):
            item_path = os.path.join(copies_dir, item)
            # Directories starting with Copie_ (post-extraction)
            if os.path.isdir(item_path) and item.startswith("Copie_"):
                copies.append(item)
            # PDF files directly in copies_anonymes_validees (pre-extraction)
            elif item.startswith("Copie_") and item.endswith(".pdf"):
                copies.append(item.replace(".pdf", ""))

    # Deduplicate and sort
    copies = sorted(set(copies), key=lambda x: int(x.replace("Copie_", "")))
    return jsonify(copies)


@app.route("/api/ocr/student/<copie_id>")
def get_ocr_student(copie_id):
    """Return the student name/id matching a copy, using correspondance.csv."""
    match = re.search(r"\d+", copie_id)
    if not match:
        return jsonify({"name": "", "firstname": "", "id": ""})
    student = database._find_student_for_copy(int(match.group()), get_ocr_project_dir())
    if not student:
        return jsonify({"name": "", "firstname": "", "id": ""})
    return jsonify(student)


@app.route("/api/ocr/pdf/<copie_id>")
def get_ocr_pdf(copie_id):
    """Serve a copy PDF or a rendered page image."""
    """Get PDF for a copy in exemple2"""
    from flask import request

    page_num = request.args.get("page", None)

    copies_dir = os.path.join(get_ocr_project_dir(), "copies_anonymes_validees")

    # First try: Copie_XX/Copie_XX.pdf
    pdf_path = os.path.join(copies_dir, copie_id, f"{copie_id}.pdf")
    if not os.path.exists(pdf_path):
        # Second try: Copie_XX.pdf directly in copies_anonymes_validees
        pdf_path = os.path.join(copies_dir, f"{copie_id}.pdf")

    if not os.path.exists(pdf_path):
        return "PDF not found", 404

    # PDF delivery and page-image delivery share one endpoint so the OCR screen
    # can use the same copy identifier for its document viewer and page editor.
    if page_num is not None:
        try:
            page_index = int(page_num) - 1
            doc = pymupdf.open(pdf_path)
            if 0 <= page_index < len(doc):
                page = doc[page_index]
                pix = page.get_pixmap(dpi=150)
                img_bytes = pix.tobytes("png")
                doc.close()
                from io import BytesIO

                return send_file(BytesIO(img_bytes), mimetype="image/png")
            doc.close()
        except Exception as e:
            print(f"Error generating page image: {e}")
            pass

    # Otherwise return the full PDF
    return send_file(pdf_path)


@app.route("/api/ocr/pages/<copie_id>")
def get_ocr_pages(copie_id):
    """Return page Markdown content and PDF page count for one copy."""
    """Get list of pages with markdown content for a copy"""
    copy_dir = get_ocr_copy_dir(copie_id)
    pages = {}

    if os.path.exists(copy_dir):
        for f in os.listdir(copy_dir):
            if f.startswith("page") and f.endswith(".md"):
                try:
                    # Remove 'page' prefix and '.md' suffix
                    page_num = f.replace("page", "").replace(".md", "")
                    # Normalize to integer for sorting, but keep original string
                    file_path = os.path.join(copy_dir, f)
                    with open(file_path, "r", encoding="utf-8") as fp:
                        pages[page_num] = fp.read()
                except Exception as e:
                    print(f"Error reading page file {f}: {e}")

    # Count total pages from PDF (check both locations)
    pdf_path = os.path.join(copy_dir, f"{copie_id}.pdf")
    if not os.path.exists(pdf_path):
        pdf_path = os.path.join(os.path.dirname(copy_dir), f"{copie_id}.pdf")
    total_pages = 0
    if os.path.exists(pdf_path):
        try:
            doc = pymupdf.open(pdf_path)
            total_pages = len(doc)
            doc.close()
        except:
            pass

    return jsonify({"pages": pages, "total_pages": total_pages})


@app.route("/api/ocr/content/<copie_id>/<page_num>", methods=["GET", "POST"])
def handle_ocr_page_content(copie_id, page_num):
    """Read or write one normalized OCR page Markdown file."""
    """Get or save markdown content for a specific page"""
    copy_dir = get_ocr_copy_dir(copie_id)

    # Normalize page_num to 2-digit format (01, 02, etc.)
    try:
        page_int = int(page_num)
        page_num_padded = f"{page_int:02d}"
    except:
        page_num_padded = page_num

    file_path = os.path.join(copy_dir, f"page{page_num_padded}.md")

    if request.method == "POST":
        content = request.json.get("content", "")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return jsonify({"success": True})
    else:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                return jsonify({"content": f.read()})
        return jsonify({"content": ""})


@app.route("/api/ocr/rebuild_extraction/<copie_id>", methods=["POST"])
def rebuild_extraction(copie_id):
    """Rebuild one copy's extraction Markdown from page files."""
    """Rebuild extraction.md from all page markdown files"""
    copy_dir = get_ocr_copy_dir(copie_id)
    extraction_path = os.path.join(copy_dir, "extraction.md")

    try:
        # Page files are the editable source of truth for OCR verification;
        # extraction.md is a derived aggregate consumed by correction.
        page_files = sorted(
            [f for f in os.listdir(copy_dir) if f.startswith("page") and f.endswith(".md")]
        )

        parts = []
        for page_file in page_files:
            page_num = page_file.replace("page", "").replace(".md", "")
            file_path = os.path.join(copy_dir, page_file)

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            if content.strip():
                parts.append(f"<!-- ====== page {page_num} ====== -->")
                parts.append(content)
            else:
                parts.append(f"<!-- ====== page {page_num} (VIDE) ====== -->")

        full_content = "\n\n".join(parts)

        with open(extraction_path, "w", encoding="utf-8") as f:
            f.write(full_content)

        return jsonify(
            {
                "success": True,
                "message": f"extraction.md regénérée avec {len(parts)} pages",
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/ocr")
def ocr_page():
    """Render the OCR batch page."""
    return render_template("ocr.html", active_page="ocr")


@app.route("/correction")
def correction_page():
    """Render the automatic correction batch page."""
    return render_template("correction.html", active_page="correction")


@app.route("/bilan")
def bilan_page():
    """Render the correction statistics page."""
    return render_template("bilan.html", active_page="bilan")


@app.route("/documentation")
def documentation_page():
    """Render the in-application documentation based on the project README."""
    return render_template("documentation.html", active_page="documentation")


@app.route("/api/bilan/export-graphs", methods=["POST"])
def export_bilan_graphs():
    """Save exported bilan PNG images in the active project directory."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("graphs"), list):
        return jsonify({"success": False, "error": "Format d'export invalide."}), 400

    allowed_names = {
        "distribution-des-notes.png",
        "difficulte-des-questions.png",
        "profil-moyen-de-la-classe.png",
        "dispersion-par-question.png",
        "resultats-detailes.png",
    }
    output_dir = config.get_path("graphiques_notes")
    os.makedirs(output_dir, exist_ok=True)
    saved_files = []

    try:
        import binascii
        for graph in data["graphs"]:
            if not isinstance(graph, dict):
                continue
            filename = graph.get("filename")
            image_data = graph.get("data")
            if filename not in allowed_names or not isinstance(image_data, str):
                continue
            if "," not in image_data:
                continue
            encoded = image_data.split(",", 1)[1]
            image_bytes = base64.b64decode(encoded, validate=True)
            if not image_bytes:
                continue
            with open(os.path.join(output_dir, filename), "wb") as image_file:
                image_file.write(image_bytes)
            saved_files.append(filename)
    except (ValueError, binascii.Error) as error:
        return jsonify({"success": False, "error": f"PNG invalide : {error}"}), 400

    return jsonify({"success": True, "directory": output_dir, "files": saved_files})


@app.route("/api/bilan/export-csv")
def export_bilan_csv():
    """Download the class scores as a UTF-8 CSV file."""
    db = None
    try:
        db = get_db(config.project_dir)
        question_count = db.execute("SELECT COUNT(*) FROM bareme").fetchone()[0]
        rows = db.execute("""
            SELECT copies.id AS copie_id,
                   copies.prenom,
                   copies.nom,
                   ROUND(SUM(CASE WHEN corrections.validee_par_prof
                                  THEN corrections.note_prof
                                  ELSE corrections.note_llm END), 2) AS total,
                   COUNT(corrections.id) AS nb_corr
            FROM copies
            LEFT JOIN corrections ON corrections.copie_id = copies.id
            GROUP BY copies.id
            ORDER BY copies.id
            """).fetchall()
        max_points = db.execute("SELECT COALESCE(SUM(points), 0) FROM bareme").fetchone()[0]

        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "Copie_ID",
                "Prenom",
                "Nom",
                "Note_brute",
                "Note_sur_20",
                "Questions_corrigees",
                "Nombre_questions",
                "Statut",
            ]
        )
        for row in rows:
            total = row["total"]
            score_out_of_twenty = (
                total / max_points * 20 if total is not None and max_points else ""
            )
            writer.writerow(
                [
                    row["copie_id"],
                    row["prenom"] or "",
                    row["nom"] or "",
                    total if total is not None else "",
                    round(score_out_of_twenty, 2) if score_out_of_twenty != "" else "",
                    row["nb_corr"],
                    question_count,
                    "Complète" if row["nb_corr"] >= question_count else "Partielle",
                ]
            )

        response = send_file(
            io.BytesIO(output.getvalue().encode("utf-8-sig")),
            mimetype="text/csv",
            as_attachment=True,
            download_name="notes-etudiants.csv",
        )
        return response
    finally:
        if db is not None:
            db.close()


@app.route("/api/bilan/stats")
def api_bilan_stats():
    """Return aggregate correction statistics from SQLite as JSON."""
    """Retourne les stats globales de la base pour le dashboard."""
    db = None
    try:
        db = get_db(config.project_dir)

        # This endpoint is read-only: all dashboard values are derived from the
        # database so the UI does not need to reconcile several file formats.
        nb_copies = db.execute("SELECT COUNT(*) FROM copies").fetchone()[0]

        # Nombre de questions dans le barème
        nb_questions = db.execute("SELECT COUNT(*) FROM bareme").fetchone()[0]

        # Nombre total de corrections
        nb_corrections = db.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]

        # Copies entièrement corrigées (ont toutes les questions)
        nb_full = db.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT copie_id, COUNT(*) as cnt FROM corrections
                GROUP BY copie_id HAVING cnt = ?
            )
        """,
            [nb_questions],
        ).fetchone()[0]

        # Notes par copie
        notes = db.execute("""
             SELECT c.copie_id,
                 copies.prenom,
                 copies.nom,
                 copies.etudiant_id,
                   ROUND(SUM(CASE WHEN c.validee_par_prof THEN c.note_prof ELSE c.note_llm END), 1) as total,
                   COUNT(*) as nb_corr
            FROM corrections c
             LEFT JOIN copies ON copies.id = c.copie_id
            GROUP BY c.copie_id
            ORDER BY c.copie_id
        """).fetchall()

        # Total points du barème
        max_points = db.execute("SELECT SUM(points) FROM bareme").fetchone()[0] or 0

        # Moyenne des points obtenus par question, en utilisant la note finale.
        question_averages = db.execute("""
            SELECT b.question,
                   b.points,
                   ROUND(AVG(CASE WHEN c.validee_par_prof THEN c.note_prof ELSE c.note_llm END), 2) AS moyenne
            FROM bareme b
            LEFT JOIN corrections c ON c.bareme_id = b.id
            GROUP BY b.id
            ORDER BY b.id
            """).fetchall()

        question_distributions = db.execute("""
            SELECT b.question,
                   b.points,
                   c.copie_id,
                   CASE WHEN c.validee_par_prof THEN c.note_prof ELSE c.note_llm END AS note
            FROM bareme b
            LEFT JOIN corrections c ON c.bareme_id = b.id
            ORDER BY b.id, c.copie_id
            """).fetchall()
        distributions = {}
        for row in question_distributions:
            question = row["question"]
            distributions.setdefault(
                question,
                {"question": question, "points": row["points"], "notes": []},
            )
            if row["note"] is not None:
                distributions[question]["notes"].append(row["note"])

        # Copies problématiques (moins de 100% des corrections)
        problematic = db.execute(
            """
            SELECT copie_id, COUNT(*) as cnt FROM corrections
            GROUP BY copie_id HAVING cnt < ?
            ORDER BY copie_id
        """,
            [nb_questions],
        ).fetchall()

        db.close()

        td_by_student_id = {s["id"]: s.get("td", "") for s in load_students()}

        return jsonify(
            {
                "nb_copies": nb_copies,
                "nb_questions": nb_questions,
                "nb_corrections": nb_corrections,
                "nb_fully_corrected": nb_full,
                "max_points": max_points,
                "question_averages": [
                    {
                        "question": row["question"],
                        "points": row["points"],
                        "moyenne": row["moyenne"],
                    }
                    for row in question_averages
                ],
                "question_distributions": list(distributions.values()),
                "notes": [
                    {
                        "copie_id": n["copie_id"],
                        "prenom": n["prenom"] or "",
                        "nom": n["nom"] or "",
                        "total": n["total"],
                        "nb_corr": n["nb_corr"],
                        "td": td_by_student_id.get(n["etudiant_id"] or "", ""),
                    }
                    for n in notes
                ],
                "problematic": [
                    {"copie_id": p["copie_id"], "missing": nb_questions - p["cnt"]}
                    for p in problematic
                ],
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    PORT = 5000
    print(f"CorrectAI running on http://127.0.0.1:{PORT}")
    app.run(debug=True, host="0.0.0.0", port=PORT)
