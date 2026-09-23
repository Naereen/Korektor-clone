"""Safety helpers for cleaning temporary Korektor test environments.

The functions in this module deliberately protect repository-like directories
from accidental deletion.  They are used by setup/teardown scripts rather
than by the production application.
"""
#!/usr/bin/env python3
"""
Script de nettoyage de l'environnement de test.
Supprime les fichiers de test temporaires.

Utilisation:
    uv run python tests/teardown_test_env.py --project-dir <dir> [--force]

Exemple:
    uv run python tests/teardown_test_env.py --project-dir exemple2_test
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

# Dossiers à protéger (NE JAMAIS SUPPRIMER)
PROTECTED_DIRS = [
    "exemple",
    "exemple3",
    "correct_ai",
    "templates",
    ".git",
    ".venv",
]


def is_protected(dir_path):
    """Vérifie si un dossier est protégé."""
    dir_name = os.path.basename(os.path.abspath(dir_path))
    return dir_name in PROTECTED_DIRS


def safe_delete(path, force=False):
    """
    Supprime un fichier ou dossier de manière sûre.

    Args:
        path: Chemin à supprimer
        force: Si True, ignore les erreurs

    Returns:
        True si suppression réussie, False sinon
    """
    if not os.path.exists(path):
        print(f"⚠️  {path} n'existe pas - ignoré")
        return True

    # Vérifier si c'est un dossier protégé
    if os.path.isdir(path) and is_protected(path):
        print(f"❌ ⚠️  Dossier protégé : {path} - NON SUPPRIMÉ")
        return False

    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
            print(f"✅ Dossier supprimé : {path}")
        else:
            os.remove(path)
            print(f"✅ Fichier supprimé : {path}")
        return True
    except Exception as e:
        print(f"❌ Erreur lors de la suppression de {path} : {e}")
        if force:
            return True
        return False


def teardown_project(project_dir, force=False):
    """
    Nettoie un projet de test.

    Args:
        project_dir: Dossier du projet à nettoyer
        force: Si True, force la suppression même en cas d'erreur
    """
    if not os.path.exists(project_dir):
        print(f"⚠️  {project_dir} n'existe pas")
        return True

    # Vérifier que ce n'est pas un dossier protégé
    if is_protected(project_dir):
        print(f"❌ ⚠️  Dossier protégé : {project_dir} - NON SUPPRIMÉ")
        return False

    # Lister les fichiers/dossiers à supprimer
    items = os.listdir(project_dir)

    # Supprimer les fichiers de test
    test_files = [
        "korektor.db",
        "korektor_backup.db",
        "test_korektor.db",
        "test_korektor_backup.db",
        "sujet.md",
        "corrige.md",
        "bareme.csv",
        "liste-etudiants.csv",
    ]

    for item in test_files:
        item_path = os.path.join(project_dir, item)
        safe_delete(item_path, force)

    # Supprimer les sous-dossiers créés pour les tests
    for item in items:
        item_path = os.path.join(project_dir, item)
        if os.path.isdir(item_path) and not is_protected(item_path):
            # Vérifier que ce n'est pas un dossier système
            if item.startswith(".") or item in PROTECTED_DIRS:
                continue
            safe_delete(item_path, force)

    # Si le dossier est vide, le supprimer aussi
    try:
        os.rmdir(project_dir)
        print(f"✅ Dossier vide supprimé : {project_dir}")
    except OSError:
        # Le dossier n'est pas vide
        pass

    return True


def main():
    """Remove the configured temporary test environment safely."""
    parser = argparse.ArgumentParser(description="Nettoyage environnement de test pour Korektor")
    parser.add_argument(
        "--project-dir",
        required=True,
        help="Dossier du projet à nettoyer (ex: exemple2_test)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Forcer la suppression même en cas d'erreur",
    )
    parser.add_argument(
        "--all-test-dirs",
        action="store_true",
        help="Nettoyer TOUS les dossiers de test (exemple2_test, exemple3_test, etc.)",
    )

    args = parser.parse_args()

    if args.all_test_dirs:
        # Trouver tous les dossiers de test (ceux qui contiennent _test)
        parent_dir = os.path.dirname(os.path.abspath(args.project_dir))
        test_dirs = [
            d
            for d in os.listdir(parent_dir)
            if d.endswith("_test") and os.path.isdir(os.path.join(parent_dir, d))
        ]

        if not test_dirs:
            print("⚠️  Aucun dossier de test trouvé (pattern: *_test)")
            return

        print(f"Nettoyage de {len(test_dirs)} dossiers de test...")
        for test_dir in test_dirs:
            full_path = os.path.join(parent_dir, test_dir)
            teardown_project(full_path, args.force)
    else:
        teardown_project(args.project_dir, args.force)

    print("\n" + "=" * 60)
    print("✅ Nettoyage terminé")
    print("=" * 60)


if __name__ == "__main__":
    main()
