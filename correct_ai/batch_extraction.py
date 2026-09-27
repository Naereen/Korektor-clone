"""OCR extraction pipeline for handwritten exam copies.

The module converts individual PDF pages into JPEG/base64 payloads and sends
them to an OpenAI-compatible vision endpoint.  ``extract_copy`` processes a
copy page by page, keeps prior page text as context, writes each page to a
Markdown file, and finally rebuilds ``extraction.md``.  Atomic lock files
prevent two workers from processing the same copy at once, while the ``force``
option intentionally removes prior page and extraction files before starting
again.

The command-line entry point and the Flask batch job both use this module.
Provider configuration is delegated to ``llm_providers.py`` and cancellation
is injected through an optional callback rather than through global state.
"""

import argparse
import base64
import logging
import os
import sys
from typing import Any, Dict, List

import pymupdf
import requests

try:
    from .llm_providers import get_provider
except ImportError:  # Support direct execution as a script.
    from llm_providers import get_provider

BASE_DIR = "copies_anonymes_validees"

# Configure logging to file for debugging
logging.basicConfig(
    filename="ocr.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("batch_extraction")


def extract_markdown(response):
    """Return the trimmed text enclosed by ``<markdown>`` tags, or ``None``."""
    logger.info(f"\n--- extract_markdown called ---")
    logger.info(f"Raw response (first 500 chars): {response[:500]}")

    start = response.find("<markdown>")
    end = response.find("</markdown>")

    if start >= 0 and end > start:
        text = response[start + len("<markdown>") : end].strip()
        logger.info(f"✓ Markdown extracted, text length: {len(text)}")
        return text

    logger.info(f"✗ No <markdown>...</markdown> tags found")
    return None


def page_to_base64(pdf_path, page_idx, dpi=150):
    """Render one PDF page at ``dpi`` and return its JPEG as base64 text."""
    doc = pymupdf.open(pdf_path)
    page = doc[page_idx]
    pix = page.get_pixmap(dpi=dpi)
    img_data = pix.tobytes("jpeg")
    doc.close()
    return base64.b64encode(img_data).decode("utf-8")


def ask_llm_with_image(provider, image_b64, prompt_text, system_prompt, timeout=600):
    """Send one vision image and its prompt to an OpenAI-compatible endpoint.

    Args:
        provider: Provider configuration containing endpoint and credentials.
        image_b64: JPEG image encoded as base64 text.
        prompt_text: User instruction sent alongside the image.
        system_prompt: System-level behavior instruction.
        timeout: HTTP timeout in seconds.
    """
    user_content: List[Dict[str, Any]] = [
        {"type": "text", "text": prompt_text},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
        },
    ]

    payload = {
        "model": provider.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {provider.api_key()}",
    }

    print(f"  Envoi à {provider.url} ({provider.model})...")
    resp = requests.post(
        provider.url,
        json=payload,
        headers=headers,
        timeout=timeout,
        verify=provider.verify_ssl,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def extract_copy(
    copy_number,
    project_dir=".",
    stop_callback=None,
    provider_name="ILAAS_OSS",
    use_ref_page1=False,
    force=False,
    inference_callback=None,
):
    """Extract and persist OCR Markdown for one numbered exam copy.

    Existing page files are reused unless ``force`` is true.  The optional
    callback is checked between expensive operations so a web job can stop
    cooperatively.
    """
    if stop_callback and stop_callback():
        print("Opération annulée (Start).")
        return False

    base_dir = os.path.join(project_dir, "copies_anonymes_validees")
    copy_dir_name = f"Copie_{copy_number:02d}"
    copy_pdf_name = f"Copie_{copy_number:02d}.pdf"

    pdf_path = os.path.join(base_dir, copy_pdf_name)
    output_dir = os.path.join(base_dir, copy_dir_name)
    output_file = os.path.join(output_dir, "extraction.md")

    if not os.path.exists(pdf_path):
        print(f"Skipping {copy_pdf_name}: File not found in {base_dir}")
        return False

    os.makedirs(output_dir, exist_ok=True)

    # Verrou atomique pour éviter que deux processus traitent la même copie
    lock_file = os.path.join(output_dir, ".lock")
    try:
        fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        if os.path.exists(output_file):
            print(f"Skipping {copy_dir_name}: {output_file} already exists.")
        else:
            print(f"Skipping {copy_dir_name}: locked by another process.")
        return True

    if force:
        for filename in os.listdir(output_dir):
            if filename == "extraction.md" or (
                filename.startswith("page") and filename.endswith(".md")
            ):
                os.remove(os.path.join(output_dir, filename))
        print(f"Forcing OCR recalculation for {copy_dir_name}.")

    # Si extraction.md existe déjà, libérer le lock et skip
    if os.path.exists(output_file):
        print(f"Skipping {copy_dir_name}: {output_file} already exists.")
        os.remove(lock_file)
        return True

    provider = get_provider(provider_name)

    # Charger sujet et corrigé comme référence pour l'OCR (vocabulaire technique, notations)
    sujet_path = os.path.join(project_dir, "sujet.md")
    corrige_path = os.path.join(project_dir, "corrige.md")
    with open(sujet_path, "r", encoding="utf-8") as f:
        sujet_content = f.read()
    with open(corrige_path, "r", encoding="utf-8") as f:
        corrige_content = f.read()

    # Count pages
    # Ouvrir le PDF une seule fois
    doc = pymupdf.open(pdf_path)
    num_pages = len(doc)

    # Extraire la page 1 comme référence si demandé (aide au déchiffrement de l'écriture)
    ref_page1_b64 = None
    if use_ref_page1 and num_pages > 0:
        pix = doc[0].get_pixmap(dpi=150)
        ref_page1_b64 = base64.b64encode(pix.tobytes("jpeg")).decode("utf-8")
        logger.info(f"Reference page 1 loaded as visual context")

    doc.close()

    logger.info(f"Processing {copy_pdf_name} ({num_pages} pages)...")
    print(f"Processing {copy_pdf_name} ({num_pages} pages)...")

    accumulated_text = ""
    page_transcriptions = []
    import time as time_mod

    # Recharger le contexte des pages déjà extraites (pour reprise)
    start_page_idx = 0
    for i in range(1, num_pages + 1):
        page_file = os.path.join(output_dir, f"page{i:02d}.md")
        if os.path.exists(page_file):
            with open(page_file, "r", encoding="utf-8") as f:
                text = f.read().strip()
            page_transcriptions.append({"page": i, "text": text, "status": "success"})
            accumulated_text += "\n" + text
            start_page_idx = i  # Dernière page extraite
            logger.info(f"Page {i} déjà extraite, ajoutée au contexte")
        else:
            break

    try:
        for page_idx in range(start_page_idx, num_pages):
            if stop_callback and stop_callback():
                print("Opération annulée (Pre-LLM).")
                return False

            page_num = page_idx + 1

            # Vérifier si la page a déjà été extraite
            page_file = os.path.join(output_dir, f"page{page_num:02d}.md")
            if os.path.exists(page_file):
                logger.info(f"  Page {page_num}: Already extracted, skipping")
                print(f"    Page {page_num}/{num_pages}: déjà extraite, skip.")
                with open(page_file, "r", encoding="utf-8") as f:
                    transcription = f.read().strip()
                page_transcriptions.append(
                    {"page": page_num, "text": transcription, "status": "success"}
                )
                accumulated_text += "\n" + transcription
                continue

            page_start_time = time_mod.time()
            print(f"  Page {page_num}/{num_pages}...")

            image_b64 = page_to_base64(pdf_path, page_idx)

            # Contexte global (sujet + corrigé) pour aider à reconnaître le vocabulaire
            global_context = (
                f"RÉFÉRENCE — SUJET DE L'EXAMEN (vocabulaire, notations) :\n"
                f"<SUJET>\n{sujet_content}\n</SUJET>\n"
                f"\nRÉFÉRENCE — CORRIGÉ OFFICIEL :\n"
                f"<CORRIGE>\n{corrige_content}\n</CORRIGE>\n"
            )

            # Build prompt with accumulated context from previous pages
            # Utiliser des balises uniques en majuscules pour éviter les conflits
            context_block = ""
            if accumulated_text:
                context_block = (
                    f"\nCONTEXTE DES PAGES PRÉCÉDENTES (à ne PAS répéter) :\n"
                    f"<CONTEXT>\n{accumulated_text}\n</CONTEXT>\n"
                )

            prompt_text = (
                "Transcris le texte de cette page de copie d'examen manuscrite en Markdown.\n"
                "La page PEUT être vide (pas de texte manuscrit).\n"
                "Ne change RIEN au texte. Ne corrige PAS. Ne génère PAS de code supplémentaire.\n"
                f"{global_context}"
                f"{context_block}\n"
                "Retourne UNIQUEMENT le texte transcrit entre les balises <markdown> et </markdown>.\n"
                "Si la page est vide, retourne <markdown></markdown>.\n"
                "Exemple: <markdown>**Q1.** Ma réponse...</markdown>"
            )

            system_prompt = (
                "Tu es un expert en transcription fidèle de copies d'examen manuscrites. "
                "Transcris EXACTEMENT ce qui est écrit, sans interpréter, sans corriger. "
                "Place le texte transcrit entre <markdown> et </markdown>. "
                "Ce qui ressemble à du code sera entouré de backticks ` si c'est dans une phrase, et de triples backticks ``` si c'est un bloc de code. Ne modifie pas le contenu du code. Les codes sont généralement en langages OCaml, C, SQL ou Python."
                "Ne retourne RIEN d'autre."
            )

            # Retry loop with JSON validation
            transcription = ""
            page_status = "success"
            max_attempts = 4
            logger.info(f"  Page {page_num}: Starting extraction (max {max_attempts} attempts)")
            for attempt in range(max_attempts):
                try:
                    logger.info(f"  Page {page_num}: Attempt {attempt+1}/{max_attempts}")

                    # Construire user_content avec éventuellement la page 1 comme référence
                    user_content: List[Dict[str, Any]] = [{"type": "text", "text": prompt_text}]

                    # Ajouter la page 1 comme référence pour les pages 2+ si demandé
                    if use_ref_page1 and page_idx >= 1 and ref_page1_b64:
                        user_content.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{ref_page1_b64}"},
                            }
                        )
                        user_content.append(
                            {
                                "type": "text",
                                "text": "La première image ci-dessus est la PAGE 1 (Exercice 0) - utilisez-la comme référence pour déchiffrer l'écriture de cet étudiant. La page à transcrire est ci-dessous.",
                            }
                        )

                    user_content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                        }
                    )

                    # Appeler le LLM avec le user_content construit
                    payload = {
                        "model": provider.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                    }
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {provider.api_key()}",
                    }
                    print(f"  Envoi à {provider.url} ({provider.model})...")
                    if inference_callback:
                        inference_callback(True)
                    try:
                        resp = requests.post(
                            provider.url,
                            json=payload,
                            headers=headers,
                            timeout=30,
                            verify=provider.verify_ssl,
                        )
                    finally:
                        if inference_callback:
                            inference_callback(False)
                    resp.raise_for_status()
                    response = resp.json()["choices"][0]["message"]["content"]

                    # Extraire le markdown entre les balises
                    transcription = extract_markdown(response)

                    if transcription is not None:
                        page_status = "success"
                        logger.info(f"  Page {page_num}: ✓ Extraction successful")
                        break

                    logger.info(f"  Page {page_num}: ✗ No <markdown> tags found")
                    print(f"    ✗ Page {page_num} Pas de balises <markdown>")
                    transcription = ""
                    page_status = "failed"
                except requests.exceptions.Timeout as e:
                    logger.error(f"  Page {page_num}: TIMEOUT: {str(e)[:100]}")
                    print(f"    ✗ Page {page_num} TIMEOUT")
                    transcription = ""
                    page_status = "failed"
                except Exception as e:
                    logger.error(f"  Page {page_num}: Exception: {str(e)[:200]}")
                    print(f"    ✗ Page {page_num} Erreur: {str(e)[:80]}")
                    transcription = ""
                    page_status = "failed"

            # Une chaîne vide est valide (page vide), seul None est une erreur
            if transcription is None:
                page_status = "failed"
                transcription = ""

            logger.info(
                f"  Page {page_num}: Final status = {page_status}, text length = {len(transcription) if transcription else 0}"
            )

            # Sauvegarder page par page avec statut
            page_file = os.path.join(output_dir, f"page{page_num:02d}.md")
            with open(page_file, "w", encoding="utf-8") as f:
                f.write(transcription)

            page_end_time = time_mod.time()
            elapsed = page_end_time - page_start_time

            if page_status == "success":
                if transcription:
                    print(f"    ✓ Page {page_num} extraite en {elapsed:.1f}s")
                else:
                    print(f"    ○ Page {page_num} VIDE en {elapsed:.1f}s")
            else:  # failed
                print(f"    ✗ Page {page_num} ÉCHOUÉE après {elapsed:.1f}s")

            page_transcriptions.append(
                {"page": page_num, "text": transcription, "status": page_status}
            )
            accumulated_text += "\n" + transcription

        # Construire extraction.md AVEC TOUTES les pages + statuts - UNE SEULE FOIS à la fin
        parts = []
        for pt in page_transcriptions:
            p = pt["page"]
            status = pt["status"]
            text = pt["text"]
            if status == "failed":
                parts.append(f"<!-- ====== page {p} (ÉCHEC D'EXTRACTION) ====== -->")
                parts.append("")
            else:
                # status == "success"
                if text:
                    parts.append(f"<!-- ====== page {p} ====== -->")
                    parts.append(text)
                else:
                    parts.append(f"<!-- ====== page {p} (VIDE) ====== -->")
                    parts.append("")

        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n\n".join(parts))

        print(f"Success! Saved to {output_file}")
        os.remove(lock_file)
        return True

    except Exception as e:
        print(f"Error processing {copy_pdf_name}: {e}")
        log_path = os.path.join(output_dir, "error.log")
        with open(log_path, "w") as f:
            f.write(str(e))
        if os.path.exists(lock_file):
            os.remove(lock_file)
        return False


def main():
    """Parse CLI options and run OCR extraction for the requested range."""
    parser = argparse.ArgumentParser(description="Batch extract text from exam copies.")
    parser.add_argument("--start", type=int, default=1, help="Start copy number (inclusive)")
    parser.add_argument("--end", type=int, default=5, help="End copy number (inclusive)")
    parser.add_argument("--project-dir", type=str, default=".", help="Project directory")
    parser.add_argument("--provider", type=str, default="ILAAS_OSS", help="LLM provider name")
    parser.add_argument(
        "--ref-page1",
        action="store_true",
        help="Use page 1 as visual reference for pages 2+",
    )

    args = parser.parse_args()

    print(
        f"Starting batch extraction from Copie {args.start} to {args.end} in {args.project_dir}..."
    )
    if args.ref_page1:
        print("  Using page 1 as visual reference for handwriting style")

    for i in range(args.start, args.end + 1):
        extract_copy(
            i,
            args.project_dir,
            provider_name=args.provider,
            use_ref_page1=args.ref_page1,
        )

    print("Batch processing complete.")


if __name__ == "__main__":
    main()
