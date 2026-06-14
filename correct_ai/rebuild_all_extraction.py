#!/usr/bin/env python3
"""
Reconstruit TOUS les extraction.md à partir des pageXX.md
- Lit UNIQUEMENT les fichiers pageXX.md (pas d'écriture)
- Écrit extraction.md dans chaque dossier Copie_XX/
- Gère les pages vides correctement
"""

import os
from pathlib import Path

PROJECT_DIR = Path("exemple2")
COPIES_DIR = PROJECT_DIR / "copies_anonymes_validees"


def rebuild_extraction_for_copy(copy_dir: Path) -> None:
    """Reconstruit extraction.md à partir des pageXX.md"""
    page_files = sorted(copy_dir.glob("page*.md"))
    if not page_files:
        print(f"⚠️  {copy_dir.name}: Aucun fichier page*.md trouvé")
        return

    parts = []
    for page_file in page_files:
        page_num = page_file.stem.replace("page", "")
        content = page_file.read_text(encoding="utf-8")

        if content.strip():
            parts.append(f"<!-- ====== page {page_num} ====== -->")
            parts.append(content)
        else:
            parts.append(f"<!-- ====== page {page_num} (VIDE) ====== -->")

    extraction_path = copy_dir / "extraction.md"
    extraction_path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    print(f"✅ {copy_dir.name}: {len(parts)} pages → extraction.md regénérée")


def main():
    print("=" * 60)
    print("Reconstruction des extraction.md à partir des pageXX.md")
    print("=" * 60)
    print()

    if not COPIES_DIR.exists():
        print(f"❌ Dossier non trouvé: {COPIES_DIR}")
        return

    for copy_dir in sorted(COPIES_DIR.iterdir()):
        if copy_dir.is_dir() and copy_dir.name.startswith("Copie_"):
            rebuild_extraction_for_copy(copy_dir)

    print()
    print("=" * 60)
    print("✅ TOUS les extraction.md ont été regénérés !")
    print("=" * 60)


if __name__ == "__main__":
    main()
