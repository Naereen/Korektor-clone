"""
Module de gestion de la base de données SQLite pour Korektor.

Ce module gère :
- L'initialisation du schéma SQLite
- Le chargement/sauvegarde des sujets, corrigés, barèmes, copies, corrections
- La protection des extractions vérifiées manuellement

Utilisation:
    from correct_ai.database import get_db, init_db, load_sujet, load_corrige, load_bareme
    
    db = get_db("exemple2")
    init_db(db)
    load_sujet(db, "exemple2/sujet.md")
    ...
"""
import os
import sqlite3
import csv
import re
from pathlib import Path
from typing import Optional, List, Dict, Any


# =============================================================================
# Configuration
# =============================================================================

DB_PATH = "korektor.db"


# =============================================================================
# Fonctions de Base de Données
# =============================================================================

def get_db(project_dir: str) -> sqlite3.Connection:
    """
    Retourne une connexion à la base SQLite du projet.
    
    Args:
        project_dir: Chemin vers le dossier du projet (ex: "exemple2")
    
    Returns:
        sqlite3.Connection: Connexion à la base de données
    """
    db_path = os.path.join(project_dir, DB_PATH)
    # Créer le répertoire s'il n'existe pas
    os.makedirs(project_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """
    Initialise les tables SQLite si elles n'existent pas.
    
    Args:
        conn: Connexion à la base de données
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sujet (
            id INTEGER PRIMARY KEY,
            contenu TEXT NOT NULL,
            date_creation DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS corrige (
            id INTEGER PRIMARY KEY,
            contenu TEXT NOT NULL,
            date_creation DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bareme (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            texte_question TEXT,
            type_qcm BOOLEAN NOT NULL,
            reponse_attendue TEXT,
            points REAL NOT NULL,
            UNIQUE(question)
        );

        CREATE TABLE IF NOT EXISTS copies (
            id INTEGER PRIMARY KEY,
            etudiant_id TEXT,
            nom TEXT,
            prenom TEXT,
            pdf_path TEXT,
            extraction_texte TEXT,
            extraction_verified BOOLEAN DEFAULT 0,
            date_extraction DATETIME
        );

        CREATE TABLE IF NOT EXISTS corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            copie_id INTEGER NOT NULL,
            bareme_id INTEGER NOT NULL,
            reponse_etudiant TEXT NOT NULL,
            -- Notes LLM
            note_llm REAL,
            commentaire_llm TEXT,
            raisonnement_llm TEXT,
            nb_bonnes_llm INTEGER DEFAULT 0,
            nb_mauvaises_llm INTEGER DEFAULT 0,
            bonus_redaction_llm INTEGER DEFAULT 0,
            date_correction_llm DATETIME DEFAULT CURRENT_TIMESTAMP,
            -- Notes Prof (prioritaires si validées)
            note_prof REAL,
            commentaire_prof TEXT,
            validee_par_prof BOOLEAN DEFAULT 0,
            date_validation DATETIME,
            FOREIGN KEY(copie_id) REFERENCES copies(id),
            FOREIGN KEY(bareme_id) REFERENCES bareme(id),
            UNIQUE(copie_id, bareme_id)
        );

        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            copie_id INTEGER,
            tokens_used INTEGER,
            processing_time_ms INTEGER,
            date_processed DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_corrections_copie ON corrections(copie_id);
        CREATE INDEX IF NOT EXISTS idx_corrections_bareme ON corrections(bareme_id);
        CREATE INDEX IF NOT EXISTS idx_corrections_validees ON corrections(copie_id) WHERE validee_par_prof = 1;
    """)
    conn.commit()


# =============================================================================
# Chargement des Données de Base
# =============================================================================

def load_sujet(conn: sqlite3.Connection, sujet_path: str) -> None:
    """
    Charge sujet.md dans la base.
    
    Args:
        conn: Connexion à la base de données
        sujet_path: Chemin vers le fichier sujet.md
    """
    with open(sujet_path, "r", encoding="utf-8") as f:
        content = f.read()
    conn.execute("INSERT OR REPLACE INTO sujet (id, contenu) VALUES (1, ?)", [content])
    conn.commit()


def load_corrige(conn: sqlite3.Connection, corrige_path: str) -> None:
    """
    Charge corrige.md dans la base.
    
    Args:
        conn: Connexion à la base de données
        corrige_path: Chemin vers le fichier corrige.md
    """
    with open(corrige_path, "r", encoding="utf-8") as f:
        content = f.read()
    conn.execute("INSERT OR REPLACE INTO corrige (id, contenu) VALUES (1, ?)", [content])
    conn.commit()


def load_bareme(conn: sqlite3.Connection, bareme_path: str, sujet_content: Optional[str] = None) -> None:
    """
    Charge bareme.csv dans la base avec texte_question extrait du sujet.
    
    Args:
        conn: Connexion à la base de données
        bareme_path: Chemin vers le fichier bareme.csv
        sujet_content: Contenu de sujet.md (optionnel, pour extraire texte_question)
    """
    # Extraire le texte des questions depuis sujet.md si disponible
    question_texts = {}
    if sujet_content:
        pattern = r'\(--(Q[\w.\-]+)\)'
        parts = re.split(pattern, sujet_content)
        for i in range(0, len(parts)-1, 2):
            question_id = parts[i+1]
            question_text = parts[i].strip()
            question_texts[question_id] = question_text
    
    with open(bareme_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            conn.execute("""
                INSERT OR IGNORE INTO bareme 
                (question, texte_question, type_qcm, reponse_attendue, points)
                VALUES (?, ?, ?, ?, ?)
            """, [
                row['question'],
                question_texts.get(row['question'], ''),
                row['qcm (oui/non)'] == 'oui',
                row['réponse attendue'],
                row['points']
            ])
    conn.commit()


# =============================================================================
# Gestion des Copies et Extractions
# =============================================================================

def save_extraction(
    conn: sqlite3.Connection,
    copy_id: int,
    pdf_path: str,
    extraction_text: str,
    etudiant_info: Optional[Dict[str, Any]] = None,
    verified: bool = False
) -> bool:
    """
    Sauvegarde une extraction OCR dans la base.
    
    ⚠️ **PROTECTION** : Si une extraction est déjà marquée comme vérifiée (extraction_verified=1),
    cette fonction ne l'écrasera PAS. Cela protège les extractions manuellement vérifiées
    dans exemple2/copies_anonymes_validees/.
    
    Args:
        conn: Connexion à la base de données
        copy_id: ID de la copie
        pdf_path: Chemin vers le PDF
        extraction_text: Texte extrait (OCR)
        etudiant_info: Dict avec 'id', 'name', 'firstname' (optionnel)
        verified: Si True, marque comme vérifié manuellement
    
    Returns:
        bool: True si sauvegardé, False si bloqué (déjà vérifié)
    """
    # Vérifier si cette copie est déjà marquée comme vérifiée
    existing = conn.execute("""
        SELECT extraction_verified FROM copies WHERE id = ?
    """, [copy_id]).fetchone()
    
    if existing and existing['extraction_verified']:
        # ❌ **PROTECTION** : Ne pas écraser une extraction vérifiée
        return False
    
    # Sauvegarder
    conn.execute("""
        INSERT OR REPLACE INTO copies 
        (id, pdf_path, extraction_texte, etudiant_id, nom, prenom, extraction_verified)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [
        copy_id,
        pdf_path,
        extraction_text,
        etudiant_info.get('id') if etudiant_info else None,
        etudiant_info.get('name') if etudiant_info else None,
        etudiant_info.get('firstname') if etudiant_info else None,
        1 if verified else 0
    ])
    conn.commit()
    return True


def load_all_extractions(conn: sqlite3.Connection, project_dir: str) -> None:
    """
    Charge toutes les extractions OCR depuis les fichiers.
    
    ⚠️ **NE CHARGE PAS** les extractions déjà vérifiées manuellement (exemple2/)
    
    Args:
        conn: Connexion à la base de données
        project_dir: Dossier du projet contenant les extractions
    """
    base_dir = os.path.join(project_dir, "copies_anonymes_validees")
    if not os.path.exists(base_dir):
        return
    
    for copy_dir in sorted(os.listdir(base_dir)):
        copy_path = os.path.join(base_dir, copy_dir)
        if not os.path.isdir(copy_path):
            continue
        if copy_dir.startswith("Copie_"):
            try:
                copy_id = int(copy_dir.split("_")[1])
            except (ValueError, IndexError):
                continue
            
            extraction_file = os.path.join(copy_path, "extraction.md")
            pdf_file = os.path.join(base_dir, f"{copy_dir}.pdf")
            
            if os.path.exists(extraction_file):
                with open(extraction_file, "r", encoding="utf-8") as f:
                    extraction_text = f.read()
                
                # ⚠️ NE PAS ÉCRASER si déjà vérifié manuellement
                existing = conn.execute("""
                    SELECT extraction_verified FROM copies WHERE id = ?
                """, [copy_id]).fetchone()
                
                if existing and existing['extraction_verified']:
                    continue
                
                # Trouver l'étudiant (optionnel)
                etudiant_info = _find_student_for_copy(copy_id, project_dir)
                
                # Stocker le PDF comme chemin RELATIF à project_dir
                # base_dir = project_dir/copies_anonymes_validees
                # donc pdf_file = project_dir/copies_anonymes_validees/Copie_01.pdf
                # on veut stocker : copies_anonymes_validees/Copie_01.pdf
                pdf_path_relatif = os.path.relpath(pdf_file, project_dir)
                
                # Sauvegarder (non vérifié par défaut)
                save_extraction(
                    conn=conn,
                    copy_id=copy_id,
                    pdf_path=pdf_path_relatif,
                    extraction_text=extraction_text,
                    etudiant_info=etudiant_info,
                    verified=False
                )
    
    conn.commit()


def _find_student_for_copy(copy_id: int, project_dir: str) -> Optional[Dict[str, str]]:
    """
    Trouve les infos de l'étudiant pour une copie donnée.
    Utilise correspondance.csv pour mapper Copie_ID → Etudiant_ID,
    puis liste-etudiants.csv pour obtenir Nom/Prénom.
    
    Args:
        copy_id: ID de la copie
        project_dir: Dossier du projet
    
    Returns:
        Dict avec 'id', 'name', 'firstname' ou None
    """
    # 1. Lire correspondance.csv pour trouver l'Etudiant_ID
    corr_file = os.path.join(project_dir, "correspondance.csv")
    etudiant_id = None
    if os.path.exists(corr_file):
        with open(corr_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if int(row.get('Copie_ID', -1)) == copy_id:
                    etudiant_id = row.get('Etudiant_ID', '').strip()
                    break
    
    # 2. Lire liste-etudiants.csv pour trouver le nom
    students_file = os.path.join(project_dir, "liste-etudiants.csv")
    if not os.path.exists(students_file):
        return None
    
    with open(students_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        students = list(reader)
    
    # Chercher par Etudiant_ID, ou fallback par position
    for student in students:
        sid = student.get('Num', '').strip() or student.get('N° étudiant', '').strip()
        if etudiant_id and sid == etudiant_id:
            return {
                'id': sid,
                'name': student.get('Nom', ''),
                'firstname': student.get('Prénom', '')
            }
    
    # Fallback: position dans la liste (moins fiable)
    if copy_id <= len(students):
        student = students[copy_id - 1]
        return {
            'id': student.get('Num', ''),
            'name': student.get('Nom', ''),
            'firstname': student.get('Prénom', '')
        }
    return None


def mark_extractions_verified(conn: sqlite3.Connection, project_dir: str, verified: bool = True) -> None:
    """
    Marque toutes les extractions d'un projet comme vérifiées manuellement.
    
    Args:
        conn: Connexion à la base de données
        project_dir: Dossier du projet
        verified: True pour marquer comme vérifié, False pour annuler
    """
    base_dir = os.path.join(project_dir, "copies_anonymes_validees")
    
    for copy_dir in sorted(os.listdir(base_dir)):
        copy_path = os.path.join(base_dir, copy_dir)
        if not os.path.isdir(copy_path):
            continue
        if copy_dir.startswith("Copie_"):
            try:
                copy_id = int(copy_dir.split("_")[1])
            except (ValueError, IndexError):
                continue
            
            conn.execute("""
                UPDATE copies SET extraction_verified = ? WHERE id = ?
            """, [1 if verified else 0, copy_id])
    
    conn.commit()


# =============================================================================
# Gestion des Corrections
# =============================================================================

def save_correction(
    conn: sqlite3.Connection,
    copy_id: int,
    bareme_id: int,
    data: Dict[str, Any],
    is_prof: bool = False
) -> None:
    """
    Sauvegarde une correction dans la base (LLM ou Prof).
    
    Args:
        conn: Connexion à la base de données
        copy_id: ID de la copie
        bareme_id: ID de la question dans le barème
        data: Dict contenant au moins 'reponse_etudiant', 'note', 'commentaire', 'raisonnement' (optionnel)
        is_prof: Si True, marque comme correction validée par le prof (prioritaire)
    """
    if is_prof:
        # Correction validée par le professeur (prioritaire)
        conn.execute("""
            INSERT OR REPLACE INTO corrections 
            (copie_id, bareme_id, reponse_etudiant, 
             note_prof, commentaire_prof, validee_par_prof, date_validation)
            VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        """, [
            copy_id,
            bareme_id,
            data.get('reponse_etudiant'),
            data.get('note'),
            data.get('commentaire')
        ])
    else:
        # Correction par LLM
        conn.execute("""
            INSERT OR REPLACE INTO corrections 
            (copie_id, bareme_id, reponse_etudiant, 
             note_llm, commentaire_llm, raisonnement_llm, 
             nb_bonnes_llm, nb_mauvaises_llm, bonus_redaction_llm, 
             date_correction_llm)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, [
            copy_id,
            bareme_id,
            data.get('reponse_etudiant'),
            data.get('note'),
            data.get('commentaire'),
            data.get('raisonnement'),
            data.get('nb_bonnes', 0),
            data.get('nb_mauvaises', 0),
            data.get('bonus_redaction', 0)
        ])
    conn.commit()


def get_previous_corrections(conn: sqlite3.Connection, current_copy_id: int) -> List[Dict[str, Any]]:
    """
    Récupère les corrections des copies 1..n-1 (avec notes finales = prof si validé, sinon LLM).
    
    Args:
        conn: Connexion à la base de données
        current_copy_id: ID de la copie courante
    
    Returns:
        Liste de dicts avec copie_id, bareme_id, question, texte_question,
        reponse_etudiant, note, commentaire, raisonnement
    """
    return conn.execute("""
        SELECT c.copie_id, c.bareme_id, b.question, b.texte_question,
               c.reponse_etudiant,
               CASE WHEN c.validee_par_prof THEN c.note_prof ELSE c.note_llm END as note,
               CASE WHEN c.validee_par_prof THEN c.commentaire_prof ELSE c.commentaire_llm END as commentaire,
               c.raisonnement_llm as raisonnement
        FROM corrections c
        JOIN bareme b ON c.bareme_id = b.id
        WHERE c.copie_id < ?
        ORDER BY c.copie_id, c.bareme_id
    """, [current_copy_id]).fetchall()


def has_corrections(conn: sqlite3.Connection, copy_id: int) -> bool:
    """
    Vérifie si une copie a TOUTES ses corrections (pas seulement quelques-unes).
    
    Args:
        conn: Connexion à la base de données
        copy_id: ID de la copie à vérifier
    
    Returns:
        bool: True si la copie a TOUTES les corrections, False sinon
    """
    # Compter les corrections de cette copie
    correction_count = conn.execute(
        "SELECT COUNT(*) FROM corrections WHERE copie_id = ?", [copy_id]
    ).fetchone()[0]
    
    # Compter le nombre total de questions dans le barème
    question_count = conn.execute("SELECT COUNT(*) FROM bareme").fetchone()[0]
    
    return correction_count >= question_count


def get_random_previous_corrections(
    conn: sqlite3.Connection,
    current_copy_id: int,
    max_count: int = 10
) -> List[Dict[str, Any]]:
    """
    Récupère aléatoirement jusqu'à max_count corrections des copies < current_copy_id.
    
    Args:
        conn: Connexion à la base de données
        current_copy_id: ID de la copie courante
        max_count: Nombre maximum de corrections à retourner
    
    Returns:
        Liste de dicts avec les corrections aléatoires
    """
    # Récupérer toutes les copies corrigées avant current_copy_id
    return conn.execute("""
        SELECT c.copie_id, c.bareme_id, b.question, b.texte_question,
               c.reponse_etudiant,
               CASE WHEN c.validee_par_prof THEN c.note_prof ELSE c.note_llm END as note,
               CASE WHEN c.validee_par_prof THEN c.commentaire_prof ELSE c.commentaire_llm END as commentaire,
               c.raisonnement_llm as raisonnement
        FROM corrections c
        JOIN bareme b ON c.bareme_id = b.id
        WHERE c.copie_id < ?
        GROUP BY c.copie_id  -- Une ligne par copie
        ORDER BY RANDOM()
        LIMIT ?
    """, [current_copy_id, max_count]).fetchall()


def get_all_extractions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Récupère toutes les extractions OCR.
    
    Args:
        conn: Connexion à la base de données
    
    Returns:
        Liste de dicts avec id, extraction_texte
    """
    return conn.execute("SELECT id, extraction_texte FROM copies ORDER BY id").fetchall()


def get_note_for_copy(conn: sqlite3.Connection, copy_id: int, bareme_id: int) -> Optional[float]:
    """
    Récupère la note finale (priorité au prof si validée, sinon LLM).
    
    Args:
        conn: Connexion à la base de données
        copy_id: ID de la copie
        bareme_id: ID de la question
    
    Returns:
        Note finale (float) ou None
    """
    row = conn.execute("""
        SELECT note_prof, note_llm, validee_par_prof 
        FROM corrections 
        WHERE copie_id = ? AND bareme_id = ?
    """, [copy_id, bareme_id]).fetchone()
    
    if row and row['validee_par_prof']:
        return row['note_prof']
    elif row:
        return row['note_llm']
    return None


def get_sujet(conn: sqlite3.Connection) -> str:
    """Récupère le sujet."""
    row = conn.execute("SELECT contenu FROM sujet LIMIT 1").fetchone()
    return row[0] if row else ""


def get_corrige(conn: sqlite3.Connection) -> str:
    """Récupère le corrige."""
    row = conn.execute("SELECT contenu FROM corrige LIMIT 1").fetchone()
    return row[0] if row else ""


def get_bareme(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Récupère toutes les questions du barème."""
    return conn.execute("SELECT * FROM bareme ORDER BY id").fetchall()


# =============================================================================
# Utilitaires
# =============================================================================

def backup_db(conn: sqlite3.Connection, backup_path: str) -> None:
    """
    Crée un backup de la base.
    
    Args:
        conn: Connexion à la base de données
        backup_path: Chemin vers le fichier de backup
    """
    # Méthode simple : copier le fichier
    db_file = conn.execute("PRAGMA database_list WHERE name = 'main'").fetchone()
    if db_file:
        import shutil
        shutil.copy(db_file['file'], backup_path)
