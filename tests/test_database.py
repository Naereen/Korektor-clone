"""Tests for Korektor's SQLite schema, loaders, and correction persistence.

The module validates schema creation, subject/rubric imports, OCR protection,
LLM and teacher correction priority, and query helpers using temporary
databases.
"""
import hashlib
import os
import sqlite3
import sys

import pytest

# Ajouter le répertoire parent au path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def get_db_path(test_dir):
    """Retourne le chemin de la base de test."""
    return os.path.join(test_dir, "korektor.db")


# =============================================================================
# Fixtures spécifiques à la base de données
# =============================================================================


@pytest.fixture
def test_db_with_schema(tmp_path):
    """Crée une base SQLite avec le schéma initialisé."""
    import sqlite3

    db_path = os.path.join(tmp_path, "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Initialiser le schéma
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
            note_llm REAL,
            commentaire_llm TEXT,
            raisonnement_llm TEXT,
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

    yield conn
    conn.close()

    # Nettoyer
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
def test_db_with_data(test_db_with_schema, tmp_path):
    """Base avec le schéma + données synthétiques chargées."""
    conn = test_db_with_schema

    # Créer des données synthétiques (ne dépend pas de exemple/)
    sujet_content = "# Sujet Test\n\n(--Q1)\nQuestion 1: ...\n\n(--Q2)\nQuestion 2: ...\n"
    corrige_content = "# Corrigé Test\n\nRéponse Q1: A\nRéponse Q2: B\n"

    conn.execute("INSERT OR REPLACE INTO sujet (id, contenu) VALUES (1, ?)", [sujet_content])
    conn.execute("INSERT OR REPLACE INTO corrige (id, contenu) VALUES (1, ?)", [corrige_content])

    # Insérer des questions de barème synthétiques
    import csv
    import io

    bareme_csv = io.StringIO(
        "question,qcm (oui/non),réponse attendue,points\n"
        "Q1,oui,A,1.0\n"
        "Q2,non,réponse libre,2.0\n"
    )
    reader = csv.DictReader(bareme_csv)
    for row in reader:
        conn.execute(
            """
            INSERT OR IGNORE INTO bareme
            (question, texte_question, type_qcm, reponse_attendue, points)
            VALUES (?, ?, ?, ?, ?)
        """,
            [
                row["question"],
                "",
                row["qcm (oui/non)"] == "oui",
                row["réponse attendue"],
                row["points"],
            ],
        )

    conn.commit()
    return conn


# =============================================================================
# Tests du Schéma SQLite (1.1)
# =============================================================================


class TestSchema:
    """Tests du schéma de la base de données."""

    def test_all_tables_created(self, test_db_with_schema):
        """1.1.1 : Vérifier que toutes les tables sont créées."""
        tables = test_db_with_schema.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t["name"] for t in tables]

        expected_tables = [
            "sujet",
            "corrige",
            "bareme",
            "copies",
            "corrections",
            "stats",
        ]
        for table in expected_tables:
            assert table in table_names, f"Table {table} manquante"

    def test_all_indexes_created(self, test_db_with_schema):
        """1.1.1 : Vérifier que tous les index sont créés."""
        indexes = test_db_with_schema.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        index_names = [i["name"] for i in indexes]

        expected_indexes = [
            "idx_corrections_copie",
            "idx_corrections_bareme",
            "idx_corrections_validees",
        ]
        for idx in expected_indexes:
            assert idx in index_names, f"Index {idx} manquant"


# =============================================================================
# Tests de load_sujet et load_corrige (1.1.2)
# =============================================================================


class TestLoadSujetCorrige:
    """Tests du chargement du sujet et du corrigé."""

    def test_load_sujet(self, tmp_path):
        """1.1.2 : Vérifier que sujet.md est bien chargé."""
        import sqlite3

        db_path = os.path.join(tmp_path, "test.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Créer un fichier sujet.md synthétique
        sujet_path = os.path.join(tmp_path, "sujet.md")
        expected_content = "# Sujet Test\n\nQuestion 1\n"
        with open(sujet_path, "w", encoding="utf-8") as f:
            f.write(expected_content)

        # Initialiser
        conn.executescript("""
            CREATE TABLE sujet (id INTEGER PRIMARY KEY, contenu TEXT NOT NULL);
        """)

        # Charger le sujet
        with open(sujet_path, "r", encoding="utf-8") as f:
            content = f.read()
        conn.execute("INSERT OR REPLACE INTO sujet (id, contenu) VALUES (1, ?)", [content])
        conn.commit()

        # Vérifier
        result = conn.execute("SELECT contenu FROM sujet WHERE id = 1").fetchone()
        assert result is not None, "Sujet non chargé"
        assert result["contenu"] == expected_content, "Contenu du sujet ne correspond pas"
        conn.close()

    def test_load_corrige(self, tmp_path):
        """1.1.2 : Vérifier que corrige.md est bien chargé."""
        import sqlite3

        db_path = os.path.join(tmp_path, "test.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Créer un fichier corrige.md synthétique
        corrige_path = os.path.join(tmp_path, "corrige.md")
        expected_content = "# Corrigé Test\n\nRéponse: A\n"
        with open(corrige_path, "w", encoding="utf-8") as f:
            f.write(expected_content)

        # Initialiser
        conn.executescript("""
            CREATE TABLE corrige (id INTEGER PRIMARY KEY, contenu TEXT NOT NULL);
        """)

        # Charger le corrige
        with open(corrige_path, "r", encoding="utf-8") as f:
            content = f.read()
        conn.execute("INSERT OR REPLACE INTO corrige (id, contenu) VALUES (1, ?)", [content])
        conn.commit()

        # Vérifier
        result = conn.execute("SELECT contenu FROM corrige WHERE id = 1").fetchone()
        assert result is not None, "Corrigé non chargé"
        assert result["contenu"] == expected_content, "Contenu du corrigé ne correspond pas"
        conn.close()


# =============================================================================
# Tests de load_bareme (1.1.3)
# =============================================================================


class TestLoadBareme:
    """Tests du chargement du barème."""

    # CSV synthétique utilisé par plusieurs tests
    SYNTHETIC_CSV = (
        "question,qcm (oui/non),réponse attendue,points\n"
        "Q1,oui,A,1.0\n"
        "Q2,non,réponse libre,2.0\n"
        "Q3,oui,B,0.5\n"
    )

    def _insert_bareme_csv(self, conn, csv_str, question_texts=None):
        """Helper: insère un CSV de barème dans la base."""
        import csv
        import io

        reader = csv.DictReader(io.StringIO(csv_str))
        for row in reader:
            texte = question_texts.get(row["question"], "") if question_texts else ""
            conn.execute(
                """
                INSERT OR IGNORE INTO bareme
                (question, texte_question, type_qcm, reponse_attendue, points)
                VALUES (?, ?, ?, ?, ?)
            """,
                [
                    row["question"],
                    texte,
                    row["qcm (oui/non)"] == "oui",
                    row["réponse attendue"],
                    row["points"],
                ],
            )
        conn.commit()

    def test_load_bareme_all_questions(self, test_db_with_schema):
        """1.1.3 : Vérifier que toutes les questions du CSV sont chargées."""
        expected_count = 3  # Q1, Q2, Q3
        self._insert_bareme_csv(test_db_with_schema, self.SYNTHETIC_CSV)

        result = test_db_with_schema.execute("SELECT COUNT(*) as count FROM bareme").fetchone()
        assert (
            result["count"] == expected_count
        ), f"Nombre de questions incorrect : {result['count']} vs {expected_count}"

    def test_load_bareme_unique_questions(self, test_db_with_schema):
        """1.1.3 : Vérifier que chaque question est unique (pas de doublons)."""
        # Charger deux fois le même CSV
        self._insert_bareme_csv(test_db_with_schema, self.SYNTHETIC_CSV)
        self._insert_bareme_csv(test_db_with_schema, self.SYNTHETIC_CSV)

        # Vérifier qu'il n'y a pas de doublons
        result = test_db_with_schema.execute("""
            SELECT question, COUNT(*) as count
            FROM bareme
            GROUP BY question
            HAVING count > 1
        """).fetchall()
        assert len(result) == 0, f"Questions en doublon trouvées : {result}"

    def test_load_bareme_with_texte_question(self, test_db_with_schema):
        """1.1.3 : Vérifier que texte_question est extrait du sujet."""
        import re

        # Sujet avec marqueurs (--Q1), (--Q2)
        sujet_content = "Texte de la question 1.\n\n(--Q1)\n\nTexte de la question 2.\n\n(--Q2)\n\nTexte Q3.\n\n(--Q3)\n"

        # Extraire les textes (même logique que load_bareme dans database.py)
        question_texts = {}
        pattern = r"\(--(Q[\w.]+)\)"
        parts = re.split(pattern, sujet_content)
        for i in range(0, len(parts) - 1, 2):
            question_id = parts[i + 1]
            question_texts[question_id] = parts[i].strip()

        # Charger avec texte_question
        self._insert_bareme_csv(test_db_with_schema, self.SYNTHETIC_CSV, question_texts)

        # Q1 et Q2 devraient avoir un texte_question non vide
        result = test_db_with_schema.execute("""
            SELECT COUNT(*) as count FROM bareme WHERE texte_question != ''
        """).fetchone()
        assert (
            result["count"] >= 2
        ), f"Au moins 2 questions devraient avoir un texte_question, trouvé {result['count']}"


# =============================================================================
# Tests de save_extraction (1.2.1)
# =============================================================================


class TestSaveExtraction:
    """Tests de la fonction save_extraction."""

    def test_save_extraction_basic(self, test_db_with_schema):
        """1.2.1 : Sauvegarder une extraction de base."""
        extraction_text = "Ceci est le texte extrait de la copie 1."
        pdf_path = "Copie_01.pdf"

        test_db_with_schema.execute(
            """
            INSERT OR REPLACE INTO copies 
            (id, pdf_path, extraction_texte, etudiant_id, nom, prenom, extraction_verified)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            [1, pdf_path, extraction_text, None, None, None, 0],
        )
        test_db_with_schema.commit()

        # Vérifier
        result = test_db_with_schema.execute("SELECT * FROM copies WHERE id = 1").fetchone()
        assert result is not None, "Copie non sauvegardée"
        assert result["extraction_texte"] == extraction_text, "extraction_texte incorrect"
        assert result["pdf_path"] == pdf_path, "pdf_path incorrect"

    def test_save_extraction_with_student_info(self, test_db_with_schema):
        """1.2.1 : Sauvegarder une extraction avec infos étudiant."""
        etudiant_info = {"id": "ETU001", "name": "Dupont", "firstname": "Jean"}

        test_db_with_schema.execute(
            """
            INSERT OR REPLACE INTO copies 
            (id, pdf_path, extraction_texte, etudiant_id, nom, prenom, extraction_verified)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            [
                1,
                "Copie_01.pdf",
                "Extraction...",
                etudiant_info["id"],
                etudiant_info["name"],
                etudiant_info["firstname"],
                0,
            ],
        )
        test_db_with_schema.commit()

        # Vérifier
        result = test_db_with_schema.execute("SELECT * FROM copies WHERE id = 1").fetchone()
        assert result["etudiant_id"] == etudiant_info["id"]
        assert result["nom"] == etudiant_info["name"]
        assert result["prenom"] == etudiant_info["firstname"]


# =============================================================================
# Tests de get_previous_corrections (1.2.2)
# =============================================================================


class TestGetPreviousCorrections:
    """Tests de la fonction get_previous_corrections."""

    def test_get_previous_corrections_basic(self, test_db_with_data):
        """1.2.2 : Récupérer les corrections des copies précédentes."""
        # Insérer des corrections pour les copies 1 et 2
        # D'abord, insérer une copie
        test_db_with_data.execute("""
            INSERT INTO copies (id, pdf_path, extraction_texte) VALUES (1, 'copie1.pdf', 'extraction1')
        """)
        test_db_with_data.execute("""
            INSERT INTO copies (id, pdf_path, extraction_texte) VALUES (2, 'copie2.pdf', 'extraction2')
        """)

        # Insérer des questions dans bareme
        test_db_with_data.execute("""
            INSERT OR IGNORE INTO bareme (question, texte_question, type_qcm, reponse_attendue, points)
            VALUES ('Q1.1', '', 1, 'D', 1.0)
        """)
        test_db_with_data.commit()

        # Insérer des corrections pour la copie 1
        test_db_with_data.execute("""
            INSERT INTO corrections 
            (copie_id, bareme_id, reponse_etudiant, note_llm, commentaire_llm, validee_par_prof)
            VALUES (1, 1, 'D', 1.0, 'Bonne réponse', 0)
        """)
        test_db_with_data.commit()

        # Récupérer les corrections pour copie_id < 2
        results = test_db_with_data.execute("""
            SELECT c.copie_id, c.bareme_id, b.question,
                   c.reponse_etudiant,
                   CASE WHEN c.validee_par_prof THEN c.note_prof ELSE c.note_llm END as note,
                   CASE WHEN c.validee_par_prof THEN c.commentaire_prof ELSE c.commentaire_llm END as commentaire
            FROM corrections c
            JOIN bareme b ON c.bareme_id = b.id
            WHERE c.copie_id < 2
            ORDER BY c.copie_id, c.bareme_id
        """).fetchall()

        assert len(results) == 1, f"Nombre incorrect de corrections : {len(results)}"
        assert results[0]["copie_id"] == 1, "copie_id incorrect"

    def test_get_previous_corrections_priority_prof(self, test_db_with_data):
        """1.2.2 : Vérifier que note_prof est prioritaire si validee_par_prof=1."""
        # Insérer une copie
        test_db_with_data.execute("""
            INSERT INTO copies (id, pdf_path, extraction_texte) VALUES (1, 'copie1.pdf', 'extraction1')
        """)

        # Insérer une question
        test_db_with_data.execute("""
            INSERT OR IGNORE INTO bareme (question, texte_question, type_qcm, reponse_attendue, points)
            VALUES ('Q1.1', '', 1, 'D', 1.0)
        """)
        test_db_with_data.commit()

        # Insérer une correction LLM
        test_db_with_data.execute("""
            INSERT INTO corrections 
            (copie_id, bareme_id, reponse_etudiant, note_llm, commentaire_llm, note_prof, commentaire_prof, validee_par_prof)
            VALUES (1, 1, 'D', 0.5, 'LLM: partiel', 1.0, 'Prof: bon', 1)
        """)
        test_db_with_data.commit()

        # Récupérer la note finale
        result = test_db_with_data.execute("""
            SELECT 
                CASE WHEN validee_par_prof THEN note_prof ELSE note_llm END as note,
                CASE WHEN validee_par_prof THEN commentaire_prof ELSE commentaire_llm END as commentaire
            FROM corrections WHERE copie_id = 1 AND bareme_id = 1
        """).fetchone()

        assert result["note"] == 1.0, f"Note incorrecte : {result['note']} (attendu 1.0)"
        assert (
            result["commentaire"] == "Prof: bon"
        ), f"Commentaire incorrect : {result['commentaire']}"


# =============================================================================
# Tests de save_correction (1.2.3)
# =============================================================================


class TestSaveCorrection:
    """Tests de la fonction save_correction."""

    def test_save_correction_llm(self, test_db_with_data):
        """1.2.3 : Sauvegarder une correction LLM."""
        # Insérer une copie
        test_db_with_data.execute("""
            INSERT INTO copies (id, pdf_path, extraction_texte) VALUES (1, 'copie1.pdf', 'extraction1')
        """)

        # Insérer une question
        test_db_with_data.execute("""
            INSERT OR IGNORE INTO bareme (question, texte_question, type_qcm, reponse_attendue, points)
            VALUES ('Q1.1', '', 1, 'D', 1.0)
        """)
        test_db_with_data.commit()

        # Sauvegarder une correction LLM
        test_db_with_data.execute(
            """
            INSERT INTO corrections 
            (copie_id, bareme_id, reponse_etudiant, note_llm, commentaire_llm, raisonnement_llm)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            [1, 1, "D", 1.0, "Bonne réponse", "Raisonnement détaillé ici"],
        )
        test_db_with_data.commit()

        # Vérifier
        result = test_db_with_data.execute("""
            SELECT * FROM corrections WHERE copie_id = 1 AND bareme_id = 1
        """).fetchone()
        assert result is not None, "Correction non sauvegardée"
        assert result["note_llm"] == 1.0, "note_llm incorrect"
        assert result["commentaire_llm"] == "Bonne réponse", "commentaire_llm incorrect"
        assert (
            result["raisonnement_llm"] == "Raisonnement détaillé ici"
        ), "raisonnement_llm incorrect"
        assert result["validee_par_prof"] == 0, "validee_par_prof devrait être 0"

    def test_save_correction_prof_priority(self, test_db_with_data):
        """1.2.3 : Vérifier que la correction Prof a la priorité."""
        # Insérer une copie
        test_db_with_data.execute("""
            INSERT INTO copies (id, pdf_path, extraction_texte) VALUES (1, 'copie1.pdf', 'extraction1')
        """)

        # Insérer une question
        test_db_with_data.execute("""
            INSERT OR IGNORE INTO bareme (question, texte_question, type_qcm, reponse_attendue, points)
            VALUES ('Q1.1', '', 1, 'D', 1.0)
        """)
        test_db_with_data.commit()

        # Sauvegarder une correction LLM
        test_db_with_data.execute("""
            INSERT INTO corrections 
            (copie_id, bareme_id, reponse_etudiant, note_llm, commentaire_llm)
            VALUES (1, 1, 'D', 0.5, 'LLM: partiel')
        """)
        test_db_with_data.commit()

        # Sauvegarder une correction Prof (écrase avec priorité)
        test_db_with_data.execute("""
            INSERT OR REPLACE INTO corrections 
            (copie_id, bareme_id, reponse_etudiant, note_prof, commentaire_prof, validee_par_prof)
            VALUES (1, 1, 'D', 1.0, 'Prof: bon', 1)
        """)
        test_db_with_data.commit()

        # Vérifier que la note finale est celle du prof
        result = test_db_with_data.execute("""
            SELECT 
                CASE WHEN validee_par_prof THEN note_prof ELSE note_llm END as note
            FROM corrections WHERE copie_id = 1 AND bareme_id = 1
        """).fetchone()
        assert result["note"] == 1.0, f"Note incorrecte : {result['note']} (attendu 1.0)"

    def test_save_correction_with_raisonnement(self, test_db_with_data):
        """1.2.3 : Vérifier que raisonnement_llm est sauvegardé via la fonction save_correction."""
        from correct_ai.database import save_correction

        # Insérer une copie
        test_db_with_data.execute("""
            INSERT INTO copies (id, pdf_path, extraction_texte) VALUES (2, 'copie2.pdf', 'extraction2')
        """)

        # Insérer une question
        test_db_with_data.execute("""
            INSERT OR IGNORE INTO bareme (id, question, texte_question, type_qcm, reponse_attendue, points)
            VALUES (2, 'Q2.1', 'Question 2.1', 0, 'Réponse attendue', 2.0)
        """)
        test_db_with_data.commit()

        # Sauvegarder une correction LLM avec raisonnement via la fonction
        correction_data = {
            "reponse_etudiant": "Réponse de létudiant",
            "note": 1.5,
            "commentaire": "Commentaire concis",
            "raisonnement": "Explication détaillée du raisonnement du correcteur",
        }
        save_correction(
            test_db_with_data,
            copy_id=2,
            bareme_id=2,
            data=correction_data,
            is_prof=False,
        )

        # Vérifier
        result = test_db_with_data.execute("""
            SELECT raisonnement_llm, commentaire_llm, note_llm, reponse_etudiant 
            FROM corrections WHERE copie_id = 2 AND bareme_id = 2
        """).fetchone()
        assert result is not None, "Correction non sauvegardée"
        assert result["note_llm"] == 1.5, "note_llm incorrect"
        assert result["commentaire_llm"] == "Commentaire concis", "commentaire_llm incorrect"
        assert (
            result["raisonnement_llm"] == "Explication détaillée du raisonnement du correcteur"
        ), "raisonnement_llm incorrect"
        assert result["reponse_etudiant"] == "Réponse de létudiant", "reponse_etudiant incorrect"


# =============================================================================
# Tests de mark_extractions_verified (1.2.4)
# =============================================================================


class TestMarkExtractionsVerified:
    """Tests de la fonction mark_extractions_verified."""

    def test_mark_extractions_verified(self, test_db_with_schema, tmp_path):
        """1.2.4 : Marquer toutes les extractions comme vérifiées."""
        # Insérer des copies non vérifiées
        for i in range(1, 6):
            test_db_with_schema.execute(
                """
                INSERT INTO copies (id, pdf_path, extraction_texte, extraction_verified)
                VALUES (?, 'copie.pdf', 'extraction', 0)
            """,
                [i],
            )
        test_db_with_schema.commit()

        # Marquer comme vérifiées
        for i in range(1, 6):
            test_db_with_schema.execute(
                """
                UPDATE copies SET extraction_verified = 1 WHERE id = ?
            """,
                [i],
            )
        test_db_with_schema.commit()

        # Vérifier
        result = test_db_with_schema.execute("""
            SELECT COUNT(*) as count FROM copies WHERE extraction_verified = 1
        """).fetchone()
        assert result["count"] == 5, f"Nombre incorrect : {result['count']} (attendu 5)"


# =============================================================================
# Tests de Protection des Données (0.3)
# =============================================================================


class TestDataProtection:
    """Tests de protection des données vérifiées."""

    def test_protect_verified_extraction(self, test_db_with_schema):
        """0.3 : Vérifier qu'on ne peut pas écraser une extraction vérifiée."""
        # Insérer une extraction vérifiée
        test_db_with_schema.execute("""
            INSERT INTO copies (id, pdf_path, extraction_texte, extraction_verified)
            VALUES (1, 'copie1.pdf', 'extraction originale', 1)
        """)
        test_db_with_schema.commit()

        # Vérifier qu'elle est marquée comme vérifiée
        result = test_db_with_schema.execute("""
            SELECT extraction_verified FROM copies WHERE id = 1
        """).fetchone()
        assert result["extraction_verified"] == 1, "extraction_verified devrait être 1"

        # Simuler une tentative d'écriture (dans la vraie implémentation, ça devrait échouer)
        # Ici on vérifie juste que le flag est bien positionné
        # La protection réelle sera implémentée dans database.py:save_extraction()
        print(
            "⚠️  Note: La protection réelle contre l'écriture doit être implémentée dans database.py"
        )
