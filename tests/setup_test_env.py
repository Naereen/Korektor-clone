#!/usr/bin/env python3
"""
Script de setup pour l'environnement de test.
Crée une base SQLite de test et charge les données.

Utilisation:
    uv run python tests/setup_test_env.py --project-dir <dir> [--init-only]

Exemple:
    uv run python tests/setup_test_env.py --project-dir exemple2_test
"""
import argparse
import os
import sys
import sqlite3
import shutil
from pathlib import Path

# Ajouter le répertoire parent au path pour importer correct_ai
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def get_db(project_dir):
    """Retourne une connexion à la base SQLite du projet."""
    db_path = os.path.join(project_dir, "korektor.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    """Initialise les tables SQLite si elles n'existent pas."""
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
            id INTEGER PRIMARY KEY,
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
            note_llm REAL,
            commentaire_llm TEXT,
            nb_bonnes_llm INTEGER DEFAULT 0,
            nb_mauvaises_llm INTEGER DEFAULT 0,
            bonus_redaction_llm INTEGER DEFAULT 0,
            date_correction_llm DATETIME DEFAULT CURRENT_TIMESTAMP,
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


def load_sujet(conn, sujet_path):
    """Charge sujet.md dans la base."""
    with open(sujet_path, "r", encoding="utf-8") as f:
        content = f.read()
    conn.execute("INSERT OR REPLACE INTO sujet (id, contenu) VALUES (1, ?)", [content])
    conn.commit()
    print(f"✅ Sujet chargé depuis {sujet_path}")


def load_corrige(conn, corrige_path):
    """Charge corrige.md dans la base."""
    with open(corrige_path, "r", encoding="utf-8") as f:
        content = f.read()
    conn.execute("INSERT OR REPLACE INTO corrige (id, contenu) VALUES (1, ?)", [content])
    conn.commit()
    print(f"✅ Corrigé chargé depuis {corrige_path}")


def load_bareme(conn, bareme_path, sujet_content=None):
    """Charge bareme.csv dans la base avec texte_question extrait du sujet."""
    import csv
    import re
    
    # Extraire le texte des questions depuis sujet.md
    question_texts = {}
    if sujet_content:
        pattern = r'\(--(Q[\w.]+)\)'
        parts = re.split(pattern, sujet_content)
        for i in range(0, len(parts)-1, 2):
            question_id = parts[i+1]
            question_text = parts[i].strip()
            question_texts[question_id] = question_text
    
    with open(bareme_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Utiliser un ID auto-incrémenté (SQLite gère ça)
            # Le champ 'question' est UNIQUE, donc on utilise NULL pour l'ID
            # et on insère par question pour éviter les doublons
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
    print(f"✅ Barème chargé depuis {bareme_path} ({reader.line_num - 1} questions)")


def load_all_extractions(conn, project_dir):
    """
    Charge toutes les extractions OCR depuis les fichiers.
    ⚠️ NE CHARGE PAS les extractions déjà vérifiées manuellement (exemple2/)
    """
    import re
    
    base_dir = os.path.join(project_dir, "copies_anonymes_validees")
    if not os.path.exists(base_dir):
        print(f"⚠️  Dossier {base_dir} non trouvé - aucune extraction chargée")
        return
    
    for copy_dir in sorted(os.listdir(base_dir)):
        # Ne traiter que les dossiers (pas les fichiers PDF directement)
        copy_path = os.path.join(base_dir, copy_dir)
        if not os.path.isdir(copy_path):
            continue
        if copy_dir.startswith("Copie_"):
            try:
                copy_id = int(copy_dir.split("_")[1])
            except (ValueError, IndexError):
                print(f"⚠️  Nom de dossier invalide : {copy_dir} - ignoré")
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
                    print(f"⏭️  Copie {copy_id} déjà vérifiée manuellement - ignorée")
                    continue
                
                # Trouver l'étudiant (optionnel)
                etudiant_info = find_student_for_copy(copy_id, project_dir)
                
                # Stocker le PDF comme chemin RELATIF à project_dir
                pdf_path_relatif = os.path.relpath(pdf_file, project_dir)
                
                # Sauvegarder l'extraction
                conn.execute("""
                    INSERT OR REPLACE INTO copies 
                    (id, pdf_path, extraction_texte, etudiant_id, nom, prenom, extraction_verified)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, [copy_id, pdf_path_relatif, extraction_text, 
                       etudiant_info.get('id') if etudiant_info else None,
                       etudiant_info.get('name') if etudiant_info else None, 
                       etudiant_info.get('firstname') if etudiant_info else None,
                       0])  # extraction_verified = 0 (non vérifiée)
    
    conn.commit()
    print(f"✅ Extractions chargées depuis {base_dir}")


def find_student_for_copy(copy_id, project_dir):
    """Trouve les infos de l'étudiant pour une copie donnée."""
    # Charger la liste des étudiants
    students_file = os.path.join(project_dir, "liste-etudiants.csv")
    if not os.path.exists(students_file):
        return None

    import csv, io
    with open(students_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Chercher la ligne d'en-tête réelle
    header_idx = None
    for i, line in enumerate(lines):
        if 'Nom' in line or 'N° étudiant' in line or 'Num' in line:
            header_idx = i
            break
    if header_idx is None:
        return None

    reader = csv.DictReader(io.StringIO(''.join(lines[header_idx:])))
    students = list(reader)

    # Trouver l'étudiant correspondant à la copie_id
    # (logique à adapter selon la structure du projet)
    if copy_id <= len(students):
        student = students[copy_id - 1]
        return {
            'id': student.get('Num'),
            'name': student.get('Nom'),
            'firstname': student.get('Prénom')
        }
    return None


def mark_extractions_verified(conn, project_dir, verified=True):
    """Marque toutes les extractions d'un projet comme vérifiées manuellement."""
    base_dir = os.path.join(project_dir, "copies_anonymes_validees")
    
    for copy_dir in sorted(os.listdir(base_dir)):
        if copy_dir.startswith("Copie_"):
            copy_id = int(copy_dir.split("_")[1])
            conn.execute("""
                UPDATE copies SET extraction_verified = ? WHERE id = ?
            """, [1 if verified else 0, copy_id])
    
    conn.commit()
    print(f"✅ Extractions marquées comme vérifiées = {verified} pour {project_dir}")


def create_backup(conn, backup_path):
    """Crée un backup de la base."""
    row = conn.execute("PRAGMA database_list").fetchone()
    if row:
        shutil.copy(row['file'], backup_path)
    print(f"✅ Backup créé : {backup_path}")


def main():
    parser = argparse.ArgumentParser(description="Setup environnement de test pour Korektor")
    parser.add_argument("--project-dir", required=True, help="Dossier du projet (ex: exemple2_test)")
    parser.add_argument("--init-only", action="store_true", help="Initialiser la base seulement (sans charger les données)")
    parser.add_argument("--backup", action="store_true", help="Créer un backup de la base existante")
    parser.add_argument("--mark-verified", action="store_true", help="Marquer les extractions comme vérifiées")
    
    args = parser.parse_args()
    
    project_dir = args.project_dir
    if not os.path.exists(project_dir):
        print(f"❌ Dossier {project_dir} non trouvé")
        sys.exit(1)
    
    # Créer le dossier s'il n'existe pas
    os.makedirs(project_dir, exist_ok=True)
    
    # Connexion à la base
    conn = get_db(project_dir)
    
    try:
        # Initialiser la base
        print("Initialisation de la base SQLite...")
        init_db(conn)
        
        if args.init_only:
            print("✅ Base initialisée (mode --init-only)")
            return
        
        # Charger les données de base
        sujet_path = os.path.join(project_dir, "sujet.md")
        corrige_path = os.path.join(project_dir, "corrige.md")
        bareme_path = os.path.join(project_dir, "bareme.csv")
        
        if os.path.exists(sujet_path):
            with open(sujet_path, "r", encoding="utf-8") as f:
                sujet_content = f.read()
            load_sujet(conn, sujet_path)
        else:
            print(f"⚠️  Fichier sujet.md non trouvé dans {project_dir}")
            sujet_content = None

        if os.path.exists(corrige_path):
            load_corrige(conn, corrige_path)
        else:
            print(f"⚠️  Fichier corrige.md non trouvé dans {project_dir}")

        if os.path.exists(bareme_path):
            load_bareme(conn, bareme_path, sujet_content)
        else:
            print(f"⚠️  Fichier bareme.csv non trouvé dans {project_dir}")
        
        # Charger les extractions
        load_all_extractions(conn, project_dir)
        
        # Marquer comme vérifiées si demandé
        if args.mark_verified:
            mark_extractions_verified(conn, project_dir, verified=True)
        
        # Créer un backup
        if args.backup:
            backup_path = os.path.join(project_dir, "korektor_backup.db")
            create_backup(conn, backup_path)
        
        print("\n" + "="*60)
        print("✅ Environnement de test configuré avec succès !")
        print(f"   Base : {os.path.join(project_dir, 'korektor.db')}")
        print(f"   Tables : sujet, corrige, bareme, copies, corrections, stats")
        print("="*60)
        
    except Exception as e:
        print(f"❌ Erreur : {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
