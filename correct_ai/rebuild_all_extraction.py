#!/usr/bin/env python3
"""Rebuild every copy-level OCR extraction from page Markdown files.

The utility scans ``copies_anonymes_validees/Copie_XX`` directories, reads
only their ``page*.md`` files, and writes a single ordered ``extraction.md``.
It preserves empty pages with explicit markers, which makes the resulting
document faithful to the PDF page sequence and useful for later OCR review.

It is intentionally independent from the LLM pipeline: no network request is
made and no page file is changed.  It can therefore be safely rerun after a
manual page correction or after a partial extraction.

Usage:
    python correct_ai/rebuild_all_extraction.py --project-dir exemple
"""

import argparse
import os
from pathlib import Path


def rebuild_extraction_for_copy(copy_dir: Path) -> None:
    """Rebuild one ``extraction.md`` from sorted ``page*.md`` files."""
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
    """Parse command-line arguments and rebuild all eligible copy folders."""
    parser = argparse.ArgumentParser(
        description="Reconstruit les extraction.md à partir des pageXX.md"
    )
    parser.add_argument("--project-dir", required=True, help="Dossier du projet (ex: exemple)")
    args = parser.parse_args()

    copies_dir = Path(args.project_dir) / "copies_anonymes_validees"

    print("=" * 60)
    print("Reconstruction des extraction.md à partir des pageXX.md")
    print("=" * 60)
    print()

    if not copies_dir.exists():
        print(f"❌ Dossier non trouvé: {copies_dir}")
        return

    for copy_dir in sorted(copies_dir.iterdir()):
        if copy_dir.is_dir() and copy_dir.name.startswith("Copie_"):
            rebuild_extraction_for_copy(copy_dir)

    print()
    print("=" * 60)
    print("✅ TOUS les extraction.md ont été regénérés !")
    print("=" * 60)


if __name__ == "__main__":
    main()
