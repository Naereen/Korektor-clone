import collections
import copy
import csv
import io
import json
import os
import shutil
import sys
import threading
import time
from collections import defaultdict

import fitz  # PyMuPDF
from flask import (Flask, jsonify, redirect, render_template, request,
                   send_file, url_for)
from pypdf import PdfReader, PdfWriter

# Import local modules
# Ensure current dir is in sys.path to import siblings if needed, though they are in the same package
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import batch_correction
import batch_correction_v2
import batch_extraction
from database import get_all_extractions
from database import get_bareme as db_get_bareme
from database import get_db
from llm_providers import get_provider

# --- Configuration & Path Management ---
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.dirname(APP_ROOT) # Go up to project root (where code is)

CONFIG_FILE = os.path.join(APP_ROOT, ".correctai_config.json")

class ProjectConfig:
    def __init__(self):
        self.project_dir = os.path.join(APP_ROOT, 'exemple')
        self.load()

    def load(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.project_dir = data.get('project_dir', APP_ROOT)
            except:
                pass

    def save(self):
        with open(CONFIG_FILE, 'w') as f:
            json.dump({'project_dir': self.project_dir}, f)

    def get_path(self, *args):
        return os.path.join(self.project_dir, *args)

    def get_output_dir(self):
        # User requested subfolders for correction: 'corrections' or keep 'copies_anonymes_validees'
        # Let's keep the legacy name for continuity, or strictly follow "sub-dossiers".
        # Current valid data is in 'copies_anonymes_validees'.
        path = self.get_path("copies_anonymes_validees")
        os.makedirs(path, exist_ok=True)
        return path

    def get_sorted_pdf_path(self):
        return self.get_path("copiesA4_sorted.pdf")

config = ProjectConfig()

app = Flask(__name__, template_folder='templates', static_folder='static')

APP_ICON_PATH = os.path.join(APP_ROOT, 'favicon-512x512.png')


@app.route('/favicon-512x512.png')
def app_icon():
    """Serve the application icon stored at the project root."""
    return send_file(APP_ICON_PATH, mimetype='image/png')

# Global state for split view
splitter_doc = None

def get_splitter_doc():
    global splitter_doc
    path = config.get_sorted_pdf_path()
    if not os.path.exists(path):
        return None
    if splitter_doc is None or splitter_doc.is_closed:
        splitter_doc = fitz.open(path)
    return splitter_doc

# --- Settings Route ---
@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        new_dir = request.form.get('project_dir')
        if new_dir and os.path.isdir(new_dir):
            config.project_dir = new_dir
            config.save()
            # Reset global state
            global splitter_doc
            if splitter_doc: splitter_doc.close()
            splitter_doc = None
            return redirect(url_for('index'))
        else:
            return render_template('settings.html', error="Dossier invalide", current_dir=config.project_dir)
    return render_template('settings.html', current_dir=config.project_dir)

# --- ROUTES: Navigation ---

@app.route('/')
def index():
    return redirect('/import')

@app.route('/import')
def import_page():
    return render_template('import.html', active_page='import', current_dir=config.project_dir)

# --- API ROUTES: Project Management ---

@app.route('/api/project/set', methods=['POST'])
def api_project_set():
    """Définit le dossier du projet."""
    data = request.json
    new_dir = data.get('project_dir', '')
    if new_dir and os.path.isdir(new_dir):
        config.project_dir = new_dir
        config.save()
        global splitter_doc
        if splitter_doc:
            splitter_doc.close()
        splitter_doc = None
        return jsonify({"success": True, "project_dir": config.project_dir})
    return jsonify({"success": False, "error": "Dossier invalide"}), 400

@app.route('/api/project/check')
def api_project_check():
    """Vérifie la structure du dossier projet."""
    project_dir = config.project_dir
    files = {
        'sujet.md': os.path.exists(os.path.join(project_dir, 'sujet.md')),
        'corrige.md': os.path.exists(os.path.join(project_dir, 'corrige.md')),
        'bareme.csv': os.path.exists(os.path.join(project_dir, 'bareme.csv')),
        'liste-etudiants.csv': os.path.exists(os.path.join(project_dir, 'liste-etudiants.csv')),
        'correspondance.csv': os.path.exists(os.path.join(project_dir, 'correspondance.csv')),
        'copiesA3.pdf': os.path.exists(os.path.join(project_dir, 'copiesA3.pdf')),
        'copiesA4_sorted.pdf': os.path.exists(os.path.join(project_dir, 'copiesA4_sorted.pdf')),
    }
    # Compter les copies
    copies_dir = os.path.join(project_dir, 'copies_anonymes_validees')
    nb_pdfs = 0
    nb_extractions = 0
    if os.path.isdir(copies_dir):
        for f in os.listdir(copies_dir):
            if f.startswith('Copie_') and f.endswith('.pdf'):
                nb_pdfs += 1
            if f.startswith('Copie_') and os.path.isdir(os.path.join(copies_dir, f)):
                nb_extractions += 1

    return jsonify({
        "project_dir": project_dir,
        "files": files,
        "nb_copies_pdf": nb_pdfs,
        "nb_extractions": nb_extractions
    })

@app.route('/api/bareme/generate', methods=['POST'])
def api_bareme_generate():
    """Génère le barème via subject_analysis.py."""
    import subprocess
    try:
        result = subprocess.run(
            ['python3', 'correct_ai/subject_analysis.py', '--project-dir', config.project_dir],
            capture_output=True, text=True, timeout=120,
            cwd=os.path.dirname(config.project_dir) if os.path.dirname(config.project_dir) else '.'
        )
        print(result.stdout)
        if result.returncode == 0:
            # Lire le barème généré
            bareme_path = os.path.join(config.project_dir, 'bareme.csv')
            bareme_data = []
            if os.path.exists(bareme_path):
                with open(bareme_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    bareme_data = list(reader)
            return jsonify({"success": True, "bareme": bareme_data, "output": result.stdout})
        else:
            return jsonify({"success": False, "error": result.stderr or result.stdout}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- UTILS: Student Loading ---
def load_students():
    csv_path = config.get_path("liste-etudiants.csv")
    students = []
    if os.path.exists(csv_path):
        try:
            with open(csv_path, 'r', newline='', encoding='utf-8') as f:
                # Read all lines to find the header
                lines = f.readlines()

            header_index = -1
            delimiter = ';' # default

            # 1. Detect header line and delimiter
            for i, line in enumerate(lines):
                # Check for comma or semi-colon
                if "Nom" in line and ("Prénom" in line or "Prenom" in line):
                     header_index = i
                     if line.count(',') > line.count(';'):
                         delimiter = ','
                     else:
                         delimiter = ';'
                     break

            if header_index != -1:
                # Re-open or use the lines from header_index
                # We can just parse the list of lines starting from header_index
                reader = csv.DictReader(lines[header_index:], delimiter=delimiter)

                for row in reader:
                    # Robust field detection
                    keys = list(row.keys()) # keys are from the header line

                    # Safe helper to find key
                    def find_key(keywords):
                        for k in keys:
                            if k and any(sub in k.lower() for sub in keywords):
                                return k
                        return None

                    id_key = find_key(['num', 'etu', 'n° étudiant'])
                    nom_key = find_key(['nom'])
                    prenom_key = find_key(['prénom', 'prenom'])

                    if id_key and nom_key:
                        s_id = row[id_key].strip()
                        s_name = row[nom_key].strip()
                        s_first = row[prenom_key].strip() if prenom_key and row[prenom_key] else ""

                        # Filter out empty or header-like repetitions if any
                        if s_id and s_name and s_id.lower() != "n° étudiant":
                            students.append({
                                "id": s_id,
                                "label": f"{s_name} {s_first} ({s_id})",
                                "name": s_name,
                                "firstname": s_first
                            })
            else:
                 print("Could not find header in students CSV")

        except Exception as e:
            print(f"Error loading students: {e}")
            import traceback
            traceback.print_exc()

    students.sort(key=lambda x: x["label"])
    return students

# --- UTILS: A3 Split Logic (Refactored from sortpdf.py) ---
def process_a3_split(input_stream, output_path, sort_booklet=True):
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
            # Reorder 4,1,2,3 -> 1,2,3,4
            # Input sequence (Split): P1L(4), P1R(1), P2L(2), P2R(3) matches visual description
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



@app.route('/split')
def split_page():
    doc = get_splitter_doc()
    if not doc:
        return "Veuillez d'abord importer un document (Étape 1).", 404

    total_items = (len(doc) + 3) // 4
    students = load_students()
    return render_template('split.html', active_page='split', total_items=total_items, students=students)

import subprocess
import threading
import time

# ... existing imports ...

# --- Batch Processing State ---
class BatchJob:
    def __init__(self):
        self.running = False
        self.stop_requested = False
        self.logs = []
        self.progress = 0
        self.total = 100
        self.current_action = "Inactif"

    def start_batch_function(self, job_type, start, end, project_dir, force=False):
        if self.running: return False
        self.running = True
        self.stop_requested = False
        self.logs = []
        self.progress = 0
        self.current_action = f"Démarrage: {job_type} [{start}-{end}]"

        def run_wrapper():
            # Sauvegarder le handler original du logger korektor
            import logging
            korektor_logger = logging.getLogger('korektor')
            original_handler = korektor_logger.handlers[0] if korektor_logger.handlers else None

            try:
                # Capture standard output/error to self.logs
                class LogCapture:
                    def __init__(self, job):
                        self.job = job
                    def write(self, text):
                        if text:
                            # Avoid appending empty newlines excessively
                            if text.strip():
                                self.job.logs.append(text)
                            # Heuristic progress update from logs
                            if "Processing" in text or "Traitem" in text:
                                # Don't overwrite calculated progress if it's running
                                pass

                            # Keep action updated
                            if text.strip() and not text.startswith("==="):
                                self.job.current_action = text.strip()[:50] # Limit length

                    def flush(self): pass

                original_stdout = sys.stdout
                sys.stdout = LogCapture(self)

                # Créer un handler personnalisé pour capturer les logs du logger
                class LogCaptureHandler(logging.StreamHandler):
                    def __init__(self, job):
                        super().__init__(sys.stdout)  # sys.stdout est déjà LogCapture
                        self.job = job

                    def emit(self, record):
                        msg = self.format(record)
                        # Ajouter directement aux logs sans passer par sys.stdout
                        if hasattr(self.job, 'logs'):
                            # The frontend concatenates log entries, so preserve the
                            # line boundary normally added by StreamHandler.emit().
                            self.job.logs.append(msg if msg.endswith('\n') else msg + '\n')
                            # Mettre à jour l'action en cours
                            if msg.strip() and not msg.startswith('[') and len(msg) < 50:
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

                    if job_type == 'extraction':
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
                             )

                             count += 1
                             self.progress = int((count / total) * 100)

                    elif job_type == 'correction':
                         # Obtenir la base de données (init_db est appelé uniquement si tables n'existent pas)
                         db = get_db(project_dir)
                         # Vérifier si les tables existent déjà pour éviter init inutile
                         from database import init_db
                         table_check = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='corrections'").fetchone()
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
                         from database import (load_bareme, load_corrige,
                                               load_sujet)

                         sujet_exists = db.execute("SELECT COUNT(*) FROM sujet").fetchone()[0] > 0
                         corrige_exists = db.execute("SELECT COUNT(*) FROM corrige").fetchone()[0] > 0
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
                         corrige_exists = db.execute("SELECT COUNT(*) FROM corrige").fetchone()[0] > 0
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
                                     "SELECT COUNT(*) FROM corrections WHERE copie_id = ?", [i]
                                 ).fetchone()[0]

                                 if existing_count > 0 and not force:
                                     print(f"⏭️  Copie {i} a déjà {existing_count}/26 corrections, passage à la suivante (utilise force=True pour écraser)")
                                     count += 1
                                     self.progress = int((count / total) * 100)
                                     continue

                                 print(f"Correcting Copy {i}/{end}...")
                                 self.current_action = f"Correction Copie {i}"

                                 # Si force=True, supprimer les corrections existantes de cette copie
                                 if force and existing_count > 0:
                                     db.execute("DELETE FROM corrections WHERE copie_id = ?", [i])
                                     db.commit()
                                     print(f"✏️  Suppression des {existing_count} corrections existantes pour la copie {i}")

                                 # Appeler la correction incrémentale v2
                                 batch_correction_v2.correct_copy_incremental(
                                     copy_id=i,
                                     provider=provider,
                                     db=db,
                                     project_dir=project_dir,
                                     test_mode=False
                                 )

                                 count += 1
                                 self.progress = int((count / total) * 100)

                             db.close()
                except Exception as e:
                     print(f"Error in batch loop: {e}")
                     import traceback
                     traceback.print_exc(file=sys.stdout)

                if self.stop_requested:
                     self.current_action = "Arrêté"
                     self.logs.append("=== Traitement interrompu ===\n")
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
        pass

    def stop(self):
        if self.running:
            self.stop_requested = True
            self.logs.append("=== Demande d'arrêt reçue... ===\n")
            return True
        return False

current_job = BatchJob()

@app.route('/process')
def process_page():
    return render_template('process.html', active_page='process')

@app.route('/api/batch/start', methods=['POST'])
def batch_start():
    data = request.json
    job_type = data.get('type') # 'extraction' or 'correction'
    req_start = data.get('start')
    req_end = data.get('end')

    # Determine range
    existing = get_copy_ids() # helper needed
    if not existing:
         return jsonify({"success": False, "error": "Aucune copie trouvée"}), 400

    start_id = req_start if req_start is not None else min(existing)
    end_id = req_end if req_end is not None else max(existing)

    if start_id < 1 or end_id > max(existing) or start_id > end_id:
        return jsonify({
            "success": False,
            "error": f"Plage invalide : choisissez une plage entre 1 et {max(existing)}.",
        }), 400

    # Launch Job
    if current_job.start_batch_function(job_type, start_id, end_id, config.project_dir, force=data.get('force', False)):
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Un job est déjà en cours"}), 409

@app.route('/api/batch/stop', methods=['POST'])
def batch_stop():
    if current_job.stop():
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Aucun job actif"}), 400

@app.route('/api/batch/status')
def batch_status():
    return jsonify({
        "running": current_job.running,
        "progress": current_job.progress,
        "action": current_job.current_action,
        "logs": current_job.logs[-50:] # Send last 50 lines
    })

def get_copy_ids():
    ids = []
    output_dir = config.get_output_dir()
    if os.path.exists(output_dir):
        for f in os.listdir(output_dir):
            if f.startswith("Copie_") and f.endswith(".pdf"):
                try:
                    ids.append(int(f.replace("Copie_", "").replace(".pdf", "")))
                except: pass
    return sorted(ids)


@app.route('/verify')
def verify_page():
    return render_template('verify.html', active_page='verify')


# --- ROUTES: API Import ---

@app.route('/api/import', methods=['POST'])
def api_import():
    try:
        file = request.files.get('file')
        sort_booklet = request.form.get('sort_booklet') == 'true'

        if not file:
            return jsonify({"success": False, "error": "No file uploaded"}), 400

        # Save temp file to disk to avoid 'cannot pickle' error with pypdf deepcopy on stream
        temp_path = config.get_path("temp_import.pdf")
        file.save(temp_path)

        try:
            process_a3_split(temp_path, config.get_sorted_pdf_path(), sort_booklet)
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

@app.route('/api/split/thumbnail/<int:page_num>')
def split_thumbnail(page_num):
    try:
        doc = get_splitter_doc()
        if not doc: return "", 404

        page_idx = page_num - 1
        if 0 <= page_idx < len(doc):
            page = doc[page_idx]
            rect = page.rect
            # Header only
            clip_rect = fitz.Rect(0, 0, rect.width, rect.height * 0.35)
            pix = page.get_pixmap(clip=clip_rect, dpi=72)
            img_data = pix.tobytes("png")
            return send_file(io.BytesIO(img_data), mimetype='image/png')
        return "Page not found", 404
    except Exception as e:
        return str(e), 500

@app.route('/api/split/process', methods=['POST'])
def split_process():
    data = request.json
    if not data: return jsonify({"success": False, "error": "No data"}), 400

    try:
        doc = get_splitter_doc()
        if not doc: return jsonify({"success": False, "error": "Doc not loaded"}), 500

        # Logic from split_copies.py
        copy_segments = []
        current_copy_segments = []
        current_student_id = None
        copy_students = []

        for item in data:
            page_num = item['page_num']
            is_continuation = item.get('is_continuation', False)
            student_id = item.get('student_id', None)
            start_idx = page_num - 1

            is_same_student = (student_id is not None and student_id == current_student_id)

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

        # Duplicate check logic... (omitted for brevity, can re-add if needed, but important)

        output_dir = config.get_output_dir()
        csv_path = config.get_path("correspondance.csv")
        students_list = load_students()
        student_info_map = {s['id']: s for s in students_list}

        with open(csv_path, "w", newline='', encoding='utf-8') as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["Copie_ID", "Fichier", "Nb_Pages", "Page_Range", "Etudiant_ID", "Nom", "Prenom"])

            for i, segments in enumerate(copy_segments):
                copy_id = i + 1
                s_id = copy_students[i] if i < len(copy_students) else ""

                s_nom = ""
                s_prenom = ""
                if s_id and s_id in student_info_map:
                    s_nom = student_info_map[s_id].get("name", "")
                    s_prenom = student_info_map[s_id].get("firstname", "")

                new_doc = fitz.open()
                total_pages = 0

                # Range
                if segments:
                    min_p = segments[0] + 1
                    last_start = segments[-1]
                    max_p = min(last_start + 4, len(doc))
                    page_range_str = f"{min_p}--{max_p}"
                else:
                    page_range_str = ""

                for start_idx in segments:
                    end_idx = min(start_idx + 4, len(doc))
                    new_doc.insert_pdf(doc, from_page=start_idx, to_page=end_idx-1)

                    # Header mask & Numbering
                    segment_first_page = new_doc[total_pages]
                    rect = segment_first_page.rect
                    header_rect = fitz.Rect(0, 0, rect.width, rect.height * 0.35)

                    shape = segment_first_page.new_shape()
                    shape.draw_rect(header_rect)
                    shape.finish(color=(1, 1, 1), fill=(1, 1, 1))
                    shape.commit()

                    text = f"Copie {copy_id}"
                    segment_first_page.insert_textbox(header_rect, text, fontsize=40, color=(0, 0, 0), align=1)

                    total_pages += (end_idx - start_idx)

                filename = f"Copie_{copy_id:02d}.pdf"
                filepath = os.path.join(output_dir, filename)
                new_doc.save(filepath)
                new_doc.close()

                writer.writerow([copy_id, filename, total_pages, page_range_str, s_id or "", s_nom, s_prenom])

        return jsonify({"success": True, "message": f"{len(copy_segments)} copies générées."})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# --- ROUTES: API Verification/Validator ---

@app.route('/api/copies')
def list_copies():
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
        pdf_files.sort(key=lambda x: int(x.split('_')[1].split('.')[0]))
        return jsonify([f.replace(".pdf", "") for f in pdf_files])

    return jsonify(copies)

@app.route('/api/bareme')
def get_bareme():
    questions = []
    path = config.get_path("bareme.csv")
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    questions.append(row)
        except Exception as e:
            print(f"Error reading bareme: {e}")
    return jsonify(questions)

@app.route('/api/pdf/<copie_id>')
def get_pdf(copie_id):
    # copie_id like "Copie_01"
    filename = f"{copie_id}.pdf"
    path = os.path.join(config.get_output_dir(), filename)
    if os.path.exists(path):
        return send_file(path)
    return "Not found", 404

# Use subfolders for data to keep clean
def get_copy_dir(copie_id):
    d = os.path.join(config.get_output_dir(), copie_id)
    if not os.path.exists(d):
        os.makedirs(d)
    return d

@app.route('/api/extraction/<copie_id>', methods=['GET', 'POST'])
def handle_extraction(copie_id):
    # Store markdown in a subfolder: copies_anonymes_validees/Copie_XX/extraction.md
    copy_dir = get_copy_dir(copie_id)
    file_path = os.path.join(copy_dir, "extraction.md")

    if request.method == 'POST':
        content = request.json.get('content', '')
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({"success": True})
    else:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return jsonify({"content": f.read()})
        return jsonify({"content": ""})

@app.route('/api/correction/<copie_id>', methods=['GET', 'POST'])
def handle_correction(copie_id):
    # Store CSV in a subfolder: copies_anonymes_validees/Copie_XX/correction.csv
    copy_dir = get_copy_dir(copie_id)
    ai_file_path = os.path.join(copy_dir, "correction.csv")
    prof_file_path = os.path.join(copy_dir, "correction_prof.csv")

    if request.method == 'POST':
        data = request.json
        if data:
            # Fix: Load bareme to get strict field order
            try:
                fieldnames = []
                bareme_path = config.get_path("bareme.csv")
                if os.path.exists(bareme_path):
                     with open(bareme_path, 'r', encoding='utf-8') as bf:
                         reader = csv.DictReader(bf)
                         fieldnames = reader.fieldnames

                if not fieldnames:
                     # Fallback if bareme not found/empty
                     fieldnames = list(data[0].keys())

                # Compulsory write to correction_prof.csv
                with open(prof_file_path, 'w', newline='', encoding='utf-8') as f:
                    # extrasaction='ignore' prevents errors if frontend sent extra props
                    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
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
            with open(target_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        return jsonify(rows)


# --- ROUTES: DB Verification (SQLite) ---

@app.route('/api/db/bareme')
def get_db_bareme():
    """Recupere le bareme depuis la base de donnees SQLite"""
    try:
        db = get_db(config.project_dir)
        bareme = db_get_bareme(db)
        return jsonify([dict(row) for row in bareme])
    except Exception as e:
        print(f"Error reading bareme from DB: {e}")
        return get_bareme()


@app.route('/api/db/copies')
def list_db_copies():
    """Liste toutes les copies depuis la base de donnees SQLite"""
    try:
        db = get_db(config.project_dir)
        copies = db.execute(
            "SELECT id, etudiant_id, nom, prenom FROM copies ORDER BY id"
        ).fetchall()
        return jsonify([
            {
                'id': row['id'],
                'etudiant_id': row['etudiant_id'],
                'nom': row['nom'],
                'prenom': row['prenom'],
            }
            for row in copies
        ])
    except Exception as e:
        print(f"Error listing copies from DB: {e}")
        return jsonify([])


@app.route('/api/db/corrections/<int:copie_id>', methods=['GET', 'POST'])
def get_db_corrections(copie_id):
    """Recupere toutes les corrections d'une copie depuis la base de donnees SQLite"""
    try:
        db = get_db(config.project_dir)

        if request.method == 'POST':
            corrections = request.get_json(silent=True)
            if not isinstance(corrections, list):
                return jsonify({'success': False, 'error': 'Format de corrections invalide'}), 400

            for correction in corrections:
                question = correction.get('question')
                if not question:
                    continue

                bareme_row = db.execute(
                    "SELECT id FROM bareme WHERE question = ?", [question]
                ).fetchone()
                if not bareme_row:
                    continue

                db.execute("""
                    UPDATE corrections
                    SET reponse_etudiant = ?,
                        note_prof = ?,
                        commentaire_prof = ?,
                        validee_par_prof = 1,
                        date_validation = CURRENT_TIMESTAMP
                    WHERE copie_id = ? AND bareme_id = ?
                """, [
                    correction.get('reponses etudiant', ''),
                    correction.get('note'),
                    correction.get('commentaires eventuels du correcteur', ''),
                    copie_id,
                    bareme_row['id'],
                ])

            db.commit()
            return jsonify({'success': True})

        corrections = db.execute("""
            SELECT
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
                c.validee_par_prof
            FROM corrections c
            JOIN bareme b ON c.bareme_id = b.id
            WHERE c.copie_id = ?
            ORDER BY b.id
        """, [copie_id]).fetchall()

        result = []
        for row in corrections:
            result.append({
                'question': row['question'],
                'texte_question': row['texte_question'],
                'qcm (oui/non)': 'oui' if row['type_qcm'] else 'non',
                'reponse attendue': row['reponse_attendue'],
                'points': row['points'],
                'reponses etudiant': row['reponse_etudiant'],
                'note': row['note'],
                'commentaires eventuels du correcteur': row['commentaire'],
                'raisonnement_llm': row['raisonnement'],
                'nbre de bonnes reponses si QCM': row['nb_bonnes_llm'],
                'nbre de mauvaises reponses si QCM': row['nb_mauvaises_llm'],
                'Redaction si QCM': row['bonus_redaction_llm'],
                'validee_par_prof': row['validee_par_prof']
            })
        return jsonify(result)
    except Exception as e:
        print(f"Error reading corrections from DB for copy {copie_id}: {e}")
        import traceback
        traceback.print_exc()
        return jsonify([])


# --- ROUTES: OCR Verification ---

@app.route('/verify_ocr')
def verify_ocr_page():
    return render_template('verify_ocr.html', active_page='verify_ocr')


def get_ocr_project_dir():
    """Get the current project directory for OCR verification"""
    return config.project_dir


def get_ocr_copy_dir(copie_id):
    """Get directory for a specific copy in the current project"""
    base_dir = os.path.join(get_ocr_project_dir(), 'copies_anonymes_validees')
    d = os.path.join(base_dir, copie_id)
    if not os.path.exists(d):
        os.makedirs(d, exist_ok=True)
    return d


@app.route('/api/ocr/copies')
def list_ocr_copies():
    """List all copies in the current project's copies_anonymes_validees"""
    copies_dir = os.path.join(get_ocr_project_dir(), 'copies_anonymes_validees')
    copies = []

    if os.path.exists(copies_dir):
        for item in os.listdir(copies_dir):
            item_path = os.path.join(copies_dir, item)
            # Directories starting with Copie_ (post-extraction)
            if os.path.isdir(item_path) and item.startswith('Copie_'):
                copies.append(item)
            # PDF files directly in copies_anonymes_validees (pre-extraction)
            elif item.startswith('Copie_') and item.endswith('.pdf'):
                copies.append(item.replace('.pdf', ''))

    # Deduplicate and sort
    copies = sorted(set(copies), key=lambda x: int(x.replace('Copie_', '')))
    return jsonify(copies)


@app.route('/api/ocr/pdf/<copie_id>')
def get_ocr_pdf(copie_id):
    """Get PDF for a copy in exemple2"""
    from flask import request

    page_num = request.args.get('page', None)

    copies_dir = os.path.join(get_ocr_project_dir(), 'copies_anonymes_validees')

    # First try: Copie_XX/Copie_XX.pdf
    pdf_path = os.path.join(copies_dir, copie_id, f"{copie_id}.pdf")
    if not os.path.exists(pdf_path):
        # Second try: Copie_XX.pdf directly in copies_anonymes_validees
        pdf_path = os.path.join(copies_dir, f"{copie_id}.pdf")

    if not os.path.exists(pdf_path):
        return "PDF not found", 404

    # If a specific page is requested, return it as an image
    if page_num is not None:
        try:
            page_index = int(page_num) - 1
            doc = fitz.open(pdf_path)
            if 0 <= page_index < len(doc):
                page = doc[page_index]
                pix = page.get_pixmap(dpi=150)
                img_bytes = pix.tobytes("png")
                doc.close()
                from io import BytesIO
                return send_file(BytesIO(img_bytes), mimetype='image/png')
            doc.close()
        except Exception as e:
            print(f"Error generating page image: {e}")
            pass

    # Otherwise return the full PDF
    return send_file(pdf_path)


@app.route('/api/ocr/pages/<copie_id>')
def get_ocr_pages(copie_id):
    """Get list of pages with markdown content for a copy"""
    copy_dir = get_ocr_copy_dir(copie_id)
    pages = {}

    if os.path.exists(copy_dir):
        for f in os.listdir(copy_dir):
            if f.startswith('page') and f.endswith('.md'):
                try:
                    # Remove 'page' prefix and '.md' suffix
                    page_num = f.replace('page', '').replace('.md', '')
                    # Normalize to integer for sorting, but keep original string
                    file_path = os.path.join(copy_dir, f)
                    with open(file_path, 'r', encoding='utf-8') as fp:
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
            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            doc.close()
        except:
            pass

    return jsonify({
        "pages": pages,
        "total_pages": total_pages
    })


@app.route('/api/ocr/content/<copie_id>/<page_num>', methods=['GET', 'POST'])
def handle_ocr_page_content(copie_id, page_num):
    """Get or save markdown content for a specific page"""
    copy_dir = get_ocr_copy_dir(copie_id)

    # Normalize page_num to 2-digit format (01, 02, etc.)
    try:
        page_int = int(page_num)
        page_num_padded = f"{page_int:02d}"
    except:
        page_num_padded = page_num

    file_path = os.path.join(copy_dir, f"page{page_num_padded}.md")

    if request.method == 'POST':
        content = request.json.get('content', '')
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({"success": True})
    else:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return jsonify({"content": f.read()})
        return jsonify({"content": ""})


@app.route('/api/ocr/rebuild_extraction/<copie_id>', methods=['POST'])
def rebuild_extraction(copie_id):
    """Rebuild extraction.md from all page markdown files"""
    copy_dir = get_ocr_copy_dir(copie_id)
    extraction_path = os.path.join(copy_dir, "extraction.md")

    try:
        # Lire TOUS les fichiers pageXX.md dans le dossier
        page_files = sorted([f for f in os.listdir(copy_dir) if f.startswith('page') and f.endswith('.md')])

        parts = []
        for page_file in page_files:
            page_num = page_file.replace('page', '').replace('.md', '')
            file_path = os.path.join(copy_dir, page_file)

            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            if content.strip():
                parts.append(f"<!-- ====== page {page_num} ====== -->")
                parts.append(content)
            else:
                parts.append(f"<!-- ====== page {page_num} (VIDE) ====== -->")

        full_content = "\n\n".join(parts)

        with open(extraction_path, 'w', encoding='utf-8') as f:
            f.write(full_content)

        return jsonify({"success": True, "message": f"extraction.md regénérée avec {len(parts)} pages"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/ocr')
def ocr_page():
    return render_template('ocr.html', active_page='ocr')

@app.route('/correction')
def correction_page():
    return render_template('correction.html', active_page='correction')

@app.route('/bilan')
def bilan_page():
    return render_template('bilan.html', active_page='bilan')

@app.route('/api/bilan/stats')
def api_bilan_stats():
    """Retourne les stats globales de la base pour le dashboard."""
    try:
        db = get_db(config.project_dir)

        # Nombre de copies
        nb_copies = db.execute("SELECT COUNT(*) FROM copies").fetchone()[0]

        # Nombre de questions dans le barème
        nb_questions = db.execute("SELECT COUNT(*) FROM bareme").fetchone()[0]

        # Nombre total de corrections
        nb_corrections = db.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]

        # Copies entièrement corrigées (ont toutes les questions)
        nb_full = db.execute("""
            SELECT COUNT(*) FROM (
                SELECT copie_id, COUNT(*) as cnt FROM corrections
                GROUP BY copie_id HAVING cnt = ?
            )
        """, [nb_questions]).fetchone()[0]

        # Notes par copie
        notes = db.execute("""
            SELECT c.copie_id,
                   ROUND(SUM(CASE WHEN c.validee_par_prof THEN c.note_prof ELSE c.note_llm END), 1) as total,
                   COUNT(*) as nb_corr
            FROM corrections c
            GROUP BY c.copie_id
            ORDER BY c.copie_id
        """).fetchall()

        # Total points du barème
        max_points = db.execute("SELECT SUM(points) FROM bareme").fetchone()[0] or 0

        # Copies problématiques (moins de 100% des corrections)
        problematic = db.execute("""
            SELECT copie_id, COUNT(*) as cnt FROM corrections
            GROUP BY copie_id HAVING cnt < ?
            ORDER BY copie_id
        """, [nb_questions]).fetchall()

        db.close()

        return jsonify({
            "nb_copies": nb_copies,
            "nb_questions": nb_questions,
            "nb_corrections": nb_corrections,
            "nb_fully_corrected": nb_full,
            "max_points": max_points,
            "notes": [{"copie_id": n['copie_id'], "total": n['total'], "nb_corr": n['nb_corr']} for n in notes],
            "problematic": [{"copie_id": p['copie_id'], "missing": nb_questions - p['cnt']} for p in problematic]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    PORT = 5000
    print(f"CorrectAI running on http://127.0.0.1:{PORT}")
    app.run(debug=True, host='0.0.0.0', port=PORT)
