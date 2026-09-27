"""
Module de correction incrémentale pour Korektor (Version 2).

Ce module implémente l'approche incrémentale où chaque correction utilise
les corrections des copies précédentes pour garantir l'uniformité des notes.

Utilisation:
    from correct_ai.batch_correction_v2 import correct_copy_incremental, correct_all_incremental

    # Corriger une seule copie
    correct_copy_incremental(copy_id=3, provider="UNISTRA_QWEN", db=db, project_dir="exemple2")

    # Corriger toutes les copies de 1 à 10
    correct_all_incremental(start=1, end=10, project_dir="exemple2", provider_name="UNISTRA_QWEN")
"""

import json
import logging
import os
import random
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import json_repair
import pymupdf

from correct_ai.image_processing import crop_and_annotate_question, is_valid_question_box
from correct_ai.llm_providers import ProviderConfig

# Ajouter le répertoire parent au path pour importer les autres modules
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# =============================================================================
# Configuration du logging
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],  # Affiche sur la console
)
logger = logging.getLogger("korektor")

from correct_ai.database import (
    get_all_extractions,
    get_bareme,
    get_corrige,
    get_db,
    get_previous_corrections,
    get_random_previous_corrections,
    get_sujet,
    has_corrections,
    init_db,
    load_all_extractions,
    load_bareme,
    load_corrige,
    load_sujet,
    save_correction,
)
from correct_ai.prompt_builder import build_incremental_prompt, estimate_prompt_tokens

CORRECTION_SYSTEM_PROMPT = (
    "Tu es un correcteur expert en informatique et en mathématiques, en CPGE filière "
    "MP2I en France. Note selon le barème et maintiens l'uniformité. Réponds avec un "
    "unique objet JSON contenant la clé questions. Pour chaque question, bbox_2d est "
    "exactement [xmin, ymin, xmax, ymax], en entiers normalisés de 0 à 1000 par "
    "rapport à l'image. La boîte doit entourer toute la réponse manuscrite. Utilise "
    "null si la localisation visuelle n'est pas fiable."
)

_THINK_BLOCK = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)
_JSON_FENCE = re.compile(r"```json\s*(.*?)```", flags=re.DOTALL | re.IGNORECASE)


def _decode_json(candidate: str) -> Any:
    """Decode JSON strictly first, then use json-repair as a bounded fallback."""
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return json_repair.loads(candidate)


def _extract_json_payload(raw_response: str) -> str:
    """Remove Qwen thinking and isolate the first decodable JSON payload."""
    clean_text = _THINK_BLOCK.sub("", raw_response).strip()
    for match in _JSON_FENCE.finditer(clean_text):
        candidate = match.group(1).strip()
        try:
            if isinstance(json.loads(candidate), (dict, list)):
                return candidate
        except (TypeError, json.JSONDecodeError):
            continue

    start = clean_text.find("{")
    end = clean_text.rfind("}")
    if start < 0 or end < start:
        return ""
    return clean_text[start : end + 1]


def _question_key(value: Any) -> str:
    """Normalize Qwen question identifiers to the rubric's Q-prefixed keys."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return text if text.upper().startswith("Q") else f"Q{text}"


def _valid_bounding_boxes(value: Any) -> List[Dict[str, Any]]:
    """Keep only well-formed, normalized boxes from a model response."""
    if isinstance(value, str):
        try:
            value = json_repair.loads(value)
        except (ValueError, TypeError):
            return []
    if is_valid_question_box(value):
        value = [{"bbox_2d": value}]
    if not isinstance(value, list):
        return []

    boxes: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        bounds = item.get("bbox_2d")
        page = item.get("page")
        if not is_valid_question_box(bounds) or (
            page is not None and (type(page) is not int or page < 1)
        ):
            continue
        box = {"bbox_2d": bounds}
        if isinstance(item.get("label"), str):
            box["label"] = item["label"]
        if page is not None:
            box["page"] = page
        boxes.append(box)
    return boxes


def _prepare_correction_data(
    data: Dict[str, Any],
    copy_id: int,
    bareme_id: int,
    pdf_path: str,
    project_dir: str,
    q_index: int,
    total_questions: int,
) -> Dict[str, Any]:
    """Serialize Qwen bounds and create an annotated or fallback crop."""
    prepared = dict(data)
    raw_boxes = data.get("bbox_2d")
    if raw_boxes is None:
        raw_boxes = data.get("bounding_boxes", [])
    boxes = _valid_bounding_boxes(raw_boxes)
    prepared["bounding_boxes"] = json.dumps(boxes, ensure_ascii=False)
    prepared["crop_image_path"] = None
    if not pdf_path:
        return prepared

    try:
        with pymupdf.open(os.path.join(project_dir, pdf_path)) as document:
            if not document:
                return prepared
            box = boxes[0] if boxes else None
            explicit_page = box.get("page") if box else data.get("page")
            estimated_page = 1 + ((max(1, q_index) - 1) * len(document) // max(1, total_questions))
            page_number = explicit_page if isinstance(explicit_page, int) else estimated_page
            page_number = min(max(1, page_number), len(document))
            relative_path = os.path.join(
                "cropped_questions", f"copie_{copy_id}_question_{bareme_id}.png"
            )
            try:
                with tempfile.TemporaryDirectory() as temp_dir:
                    page_image = os.path.join(temp_dir, "page.png")
                    document[page_number - 1].get_pixmap(dpi=150, alpha=False).save(page_image)
                    result_path = crop_and_annotate_question(
                        page_image,
                        box["bbox_2d"] if box else None,
                        os.path.join(project_dir, relative_path),
                        q_index,
                        total_questions,
                    )
                if result_path and os.path.isfile(result_path):
                    prepared["crop_image_path"] = relative_path
                    if not boxes:
                        prepared["bounding_boxes"] = json.dumps(
                            [{"bbox_2d": None, "fallback": True}], ensure_ascii=False
                        )
            except (OSError, ValueError, RuntimeError) as error:
                logger.warning("Boîte non recadrable pour la copie %s : %s", copy_id, error)
    except Exception as error:
        logger.warning("Recadrage impossible pour la copie %s : %s", copy_id, error)
    return prepared


def _fit_previous_corrections_to_prompt(
    sujet: str,
    corrige: str,
    bareme: List[Dict[str, Any]],
    previous_corrections: List[Dict[str, Any]],
    current_extraction: str,
) -> List[Dict[str, Any]]:
    """Keep complete recent copies while staying within the prompt budget."""
    # https://www.ilaas.fr/liste-des-modeles-llms/
    max_tokens = int(os.getenv("KOREKTOR_MAX_PROMPT_TOKENS", "200000"))
    selected: List[Dict[str, Any]] = []
    copy_ids = list(dict.fromkeys(corr["copie_id"] for corr in previous_corrections))

    for copy_id in reversed(copy_ids):
        candidate = [corr for corr in previous_corrections if corr["copie_id"] == copy_id]
        trial = candidate + selected
        prompt_tokens = estimate_prompt_tokens(sujet, corrige, bareme, trial, current_extraction)
        if prompt_tokens > max_tokens:
            continue
        selected = trial

    selected_copy_ids = set(corr["copie_id"] for corr in selected)
    logger.info(
        f"📚 Contexte précédent réduit à {len(selected_copy_ids)} copie(s), "
        f"{len(selected)} correction(s), budget {max_tokens} tokens"
    )
    return sorted(selected, key=lambda corr: (corr["copie_id"], corr["bareme_id"]))


def parse_llm_response(response: str) -> Dict[str, Any]:
    """
    Parse la réponse du LLM en JSON.

    Utilise json_repair pour corriger les réponses mal formatées.

    Args:
        response: Réponse brute du LLM

    Returns:
        Dict: Dictionnaire parsé avec {question: {note: ..., commentaire: ..., reponse_etudiant: ...}}

    """
    if not isinstance(response, str) or not response.strip():
        return {}

    try:
        payload = _extract_json_payload(response)
        if not payload:
            return {}
        result = _decode_json(payload)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        logger.warning("Réponse JSON du LLM inexploitable : %s", error)
        return {}
    if isinstance(result, dict) and isinstance(result.get("questions"), list):
        normalized: Dict[str, Any] = {}
        for item in result["questions"]:
            if not isinstance(item, dict) or "num_question" not in item:
                continue
            correction = dict(item)
            correction.setdefault("reponse_etudiant", "")
            correction.setdefault("raisonnement", "")
            normalized[_question_key(item["num_question"])] = correction
        return normalized
    if isinstance(result, list):
        return {
            item.get("question", str(index)): item
            for index, item in enumerate(result)
            if isinstance(item, dict)
        }
    return result if isinstance(result, dict) else {}


def correct_copy_incremental(
    copy_id: int,
    provider: Union[ProviderConfig, Dict[str, Any]],
    db: sqlite3.Connection,
    project_dir: str,
    test_mode: bool = False,
    inference_callback=None,
) -> bool:
    """
    Corrige une copie en mode incrémental.

    Le prompt inclut :
    - Sujet + Corrigé (contexte global)
    - Toutes les extractions OCR (contexte complet)
    - 10 corrections précédentes aléatoires pour uniformité
    - La copie courante en PDF (pour vérification visuelle)

    Args:
        copy_id: ID de la copie à corriger
        provider: Configuration du provider LLM
        db: Connexion à la base de données
        project_dir: Dossier du projet

    Returns:
        bool: True si succès, False si échec
    """
    # 0. Vérifier si la copie est déjà corrigée
    if has_corrections(db, copy_id):
        logger.info(f"⏭️  Copie {copy_id} déjà corrigée, passage à la suivante")
        return True

    # Tentatives (max 2)
    for attempt in range(2):
        try:
            # 1. Récupérer les données
            sujet = get_sujet(db)
            corrige = get_corrige(db)
            bareme = get_bareme(db)

            # Corrections des copies précédentes pour uniformité (10 aléatoires)
            previous_corrections = get_random_previous_corrections(db, copy_id, max_count=10)

            # Afficher les copies sélectionnées pour le contexte
            if previous_corrections:
                copy_ids = sorted(set(corr["copie_id"] for corr in previous_corrections))
                logger.info(f"📚 Copies sélectionnées pour le contexte: {copy_ids}")
            else:
                logger.info("📚 Aucune correction précédente disponible")

            # Données de la copie courante
            current_copy = db.execute(
                """
                SELECT pdf_path, extraction_texte FROM copies WHERE id = ?
            """,
                [copy_id],
            ).fetchone()

            if not current_copy:
                logger.error(f"❌ Copie {copy_id} non trouvée dans la base")
                return False

            previous_corrections = _fit_previous_corrections_to_prompt(
                sujet,
                corrige,
                bareme,
                previous_corrections,
                current_copy["extraction_texte"],
            )

            # 2. Construire le prompt
            prompt = build_incremental_prompt(
                sujet=sujet,
                corrige=corrige,
                bareme=bareme,
                previous_corrections=previous_corrections,
                current_pdf_path=current_copy["pdf_path"],
                current_extraction=current_copy["extraction_texte"],
            )

            # Afficher infos sur le prompt
            from correct_ai.prompt_builder import estimate_prompt_tokens

            prompt_token_count = estimate_prompt_tokens(
                sujet,
                corrige,
                bareme,
                previous_corrections,
                current_copy["extraction_texte"],
            )
            logger.info(f"📄 Prompt: {len(prompt)} caractères, ~{prompt_token_count} tokens")
            logger.info(f"📄 Extrait: {prompt[:500]}...")

            # 3. Appeler le LLM
            # En mode test, utiliser une réponse mockée
            if test_mode:
                response = '{"Q0": {"note": 1.0, "commentaire": "Test - Mode mock", "raisonnement": "Raisonnement test pour validation du format", "reponse_etudiant": "Test"}}'
            else:
                from correct_ai.llm_providers import (
                    ask_llm_with_pdf,
                    ask_llm_with_progress,
                    get_provider,
                )

                if isinstance(provider, str):
                    provider_name = provider
                elif isinstance(provider, dict):
                    provider_name = provider.get("name")
                else:
                    # Accept ProviderConfig instances imported through either module path.
                    provider_name = getattr(provider, "name", None)

                if not provider_name:
                    raise ValueError("Configuration provider invalide: nom absent")
                p = get_provider(provider_name)

                # Chemin absolu du PDF
                pdf_abs_path = os.path.join(project_dir, current_copy["pdf_path"])

                # Callback pour afficher la progression
                def progress_callback(msg):
                    """Forward streaming progress messages to the Korektor logger."""
                    logger.info(msg)

                try:
                    logger.info(f"Envoi du prompt + PDF à {p.url} (modèle: {p.model})...")
                    response = ask_llm_with_pdf(
                        provider=p,
                        pdf_path=pdf_abs_path,
                        prompt_text=prompt,
                        system_prompt=CORRECTION_SYSTEM_PROMPT,
                        inference_callback=inference_callback,
                    )
                except Exception as e:
                    logger.error(f"❌ Erreur lors de l'appel LLM pour la copie {copy_id}: {e}")
                    # Si le prompt est trop grand, réessayer sans images (texte seul)
                    error_text = str(e).lower()
                    is_oversized_request = (
                        getattr(getattr(e, "response", None), "status_code", None) == 413
                    )
                    if (
                        is_oversized_request
                        or "request entity too large" in error_text
                        or "context length" in error_text
                        or "tokens" in error_text
                    ):
                        logger.warning("⚠️  Prompt trop grand - passage en mode texte seul...")
                        try:
                            response = ask_llm_with_progress(
                                provider=p,
                                prompt_text=prompt,
                                system_prompt=CORRECTION_SYSTEM_PROMPT,
                                progress_callback=progress_callback,
                                inference_callback=inference_callback,
                            )
                        except Exception as e2:
                            # Dernier recours : retirer les corrections précédentes
                            logger.warning("⚠️  Toujours trop grand - réduction du contexte...")
                            prompt_simple = build_incremental_prompt(
                                sujet=sujet,
                                corrige=corrige,
                                bareme=bareme,
                                previous_corrections=[],
                                current_pdf_path=current_copy["pdf_path"],
                                current_extraction=current_copy["extraction_texte"],
                            )
                            try:
                                response = ask_llm_with_progress(
                                    provider=p,
                                    prompt_text=prompt_simple,
                                    system_prompt=CORRECTION_SYSTEM_PROMPT,
                                    progress_callback=progress_callback,
                                    inference_callback=inference_callback,
                                )
                            except Exception as e3:
                                logger.error(f"❌ Erreur même avec prompt réduit: {e3}")
                                if attempt < 1:
                                    logger.warning(
                                        f"⚠️  Tentative {attempt + 1}/2 échouée, retry dans 2s..."
                                    )
                                    time.sleep(2)
                                    continue
                                else:
                                    return False
                    else:
                        if attempt < 1:
                            logger.warning(
                                f"⚠️  Tentative {attempt + 1}/2 échouée, retry dans 2s..."
                            )
                            time.sleep(2)
                            continue
                        else:
                            return False

            # 4. Parser et sauvegarder dans SQLite
            # DEBUG: Sauvegarder la réponse brute pour diagnostic
            debug_dir = os.path.join(project_dir, "debug_responses")
            os.makedirs(debug_dir, exist_ok=True)
            with open(
                os.path.join(debug_dir, f"copie_{copy_id}_attempt_{attempt + 1}.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(response)
            logger.info(
                f"💾 Réponse brute sauvegardée dans {debug_dir}/copie_{copy_id}_attempt_{attempt + 1}.txt"
            )

            try:
                correction_data = parse_llm_response(response)
            except Exception as e:
                logger.error(
                    f"❌ Erreur lors du parsing de la réponse LLM pour la copie {copy_id}: {e}"
                )
                logger.error(f"   Réponse brute: {response[:200]}...")
                if attempt < 1:
                    logger.warning(f"⚠️  Tentative {attempt + 1}/2 échouée, retry dans 2s...")
                    time.sleep(2)
                    continue
                else:
                    return False

            # 5. Sauvegarder dans la base
            # Mapper les questions du JSON vers bareme_id
            bareme_map = {b["question"]: b["id"] for b in bareme}
            question_positions = {b["question"]: index for index, b in enumerate(bareme, start=1)}
            if not any(
                bareme_map.get(question) and isinstance(data, dict)
                for question, data in correction_data.items()
            ):
                logger.warning("Aucune question exploitable pour la copie %s", copy_id)
                if attempt < 1:
                    time.sleep(2)
                    continue
                return False

            saved_count = 0
            for question, data in correction_data.items():
                bareme_id = bareme_map.get(question)
                if bareme_id and isinstance(data, dict):
                    prepared = _prepare_correction_data(
                        data,
                        copy_id,
                        bareme_id,
                        current_copy["pdf_path"],
                        project_dir,
                        question_positions[question],
                        len(bareme),
                    )
                    save_correction(db, copy_id, bareme_id, prepared, is_prof=False)
                    saved_count += 1

            logger.info(
                f"✅ Copie {copy_id} corrigée avec succès ({saved_count} corrections sauvegardées)"
            )
            return True

        except Exception as e:
            if attempt < 1:
                logger.warning(f"⚠️  Tentative {attempt + 1}/2 échouée: {e}, retry dans 2s...")
                time.sleep(2)
                continue
            else:
                logger.error(f"❌ Copie {copy_id} échouée après 2 tentatives: {e}")
                return False

    return False


def correct_all_incremental(
    start: int,
    end: int,
    project_dir: str,
    provider_name: str = "ILAAS_OSS",
    test_mode: bool = False,
) -> None:
    """
    Corrige toutes les copies de start à end en mode incrémental.

    Args:
        start: ID de la première copie à corriger
        end: ID de la dernière copie à corriger
        project_dir: Dossier du projet
        provider_name: Nom du provider LLM
        test_mode: Si True, utilise la configuration test (gratuit, sans thinking)
    """
    from correct_ai.llm_providers import get_provider

    db = get_db(project_dir)
    init_db(db)

    # Charger sujet, corrige, bareme
    sujet_path = os.path.join(project_dir, "sujet.md")
    corrige_path = os.path.join(project_dir, "corrige.md")
    bareme_path = os.path.join(project_dir, "bareme.csv")

    if os.path.exists(sujet_path):
        with open(sujet_path, "r", encoding="utf-8") as f:
            sujet_content = f.read()
        load_sujet(db, sujet_path)
    else:
        logger.warning(f"⚠️  Fichier sujet.md non trouvé dans {project_dir}")
        sujet_content = ""

    if os.path.exists(corrige_path):
        load_corrige(db, corrige_path)
    else:
        logger.warning(f"⚠️  Fichier corrige.md non trouvé dans {project_dir}")

    if os.path.exists(bareme_path):
        load_bareme(db, bareme_path, sujet_content)
    else:
        logger.warning(f"⚠️  Fichier bareme.csv non trouvé dans {project_dir}")

    # Charger les extractions OCR existantes
    load_all_extractions(db, project_dir)

    # Corriger séquentiellement
    provider = get_provider(provider_name)

    for copy_id in range(start, end + 1):
        # Passer test_mode directement à correct_copy_incremental
        logger.info(f"--- Début correction copie {copy_id}/{end} ---")
        success = correct_copy_incremental(copy_id, provider, db, project_dir, test_mode=test_mode)
        if not success:
            logger.error(f"❌ Arrêt à la copie {copy_id}")
            break

    db.close()


def correct_copy_incremental_mock(
    copy_id: int,
    db: sqlite3.Connection,
    mock_response: Dict[str, Any],
    project_dir: str,
) -> bool:
    """
    Corrige une copie avec une réponse LLM mockée (pour les tests).

    Args:
        copy_id: ID de la copie
        db: Connexion à la base
        mock_response: Réponse LLM mockée (JSON)
        project_dir: Dossier du projet

    Returns:
        bool: True si succès
    """
    try:
        # 1. Récupérer les données
        sujet = get_sujet(db)
        corrige = get_corrige(db)
        bareme = get_bareme(db)
        previous_corrections = get_previous_corrections(db, copy_id)

        current_copy = db.execute(
            """
            SELECT pdf_path, extraction_texte FROM copies WHERE id = ?
        """,
            [copy_id],
        ).fetchone()

        if not current_copy:
            logger.error(f"❌ Copie {copy_id} non trouvée dans la base")
            return False

        # 2. Construire le prompt (pour vérification)
        prompt = build_incremental_prompt(
            sujet=sujet,
            corrige=corrige,
            bareme=bareme,
            previous_corrections=previous_corrections,
            current_pdf_path=current_copy["pdf_path"],
            current_extraction=current_copy["extraction_texte"],
        )

        # 3. Utiliser la réponse mockée
        correction_data = mock_response

        # 4. Sauvegarder dans la base
        bareme_map = {b["question"]: b["id"] for b in bareme}
        question_positions = {b["question"]: index for index, b in enumerate(bareme, start=1)}

        saved_count = 0
        for question, data in correction_data.items():
            bareme_id = bareme_map.get(question)
            if bareme_id and isinstance(data, dict):
                prepared = _prepare_correction_data(
                    data,
                    copy_id,
                    bareme_id,
                    current_copy["pdf_path"],
                    project_dir,
                    question_positions[question],
                    len(bareme),
                )
                save_correction(db, copy_id, bareme_id, prepared, is_prof=False)
                saved_count += 1

        logger.info(f"✅ Copie {copy_id} corrigée avec succès (mock, {saved_count} corrections)")
        return True

    except Exception as e:
        logger.error(f"❌ Erreur lors de la correction mock de la copie {copy_id}: {e}")
        import traceback

        traceback.print_exc()
        return False


def get_stats_for_copy(db: sqlite3.Connection, copy_id: int) -> Dict[str, Any]:
    """
    Récupère les statistiques pour une copie.

    Args:
        db: Connexion à la base
        copy_id: ID de la copie

    Returns:
        Dict avec les stats de la copie
    """
    result = db.execute(
        """
        SELECT 
            COUNT(*) as total_corrections,
            AVG(note_llm) as avg_note_llm,
            AVG(note_prof) as avg_note_prof
        FROM corrections 
        WHERE copie_id = ?
    """,
        [copy_id],
    ).fetchone()

    return dict(result) if result else {}


# =============================================================================
# CLI - Exécution en ligne de commande
# =============================================================================


def main():
    """Parse CLI options and run the incremental correction batch."""
    import argparse

    parser = argparse.ArgumentParser(description="Correction incrémentale de copies avec Korektor")
    parser.add_argument("--project-dir", required=True, help="Dossier du projet (ex: exemple2)")
    parser.add_argument("--start", type=int, required=True, help="Première copie à corriger")
    parser.add_argument("--end", type=int, required=True, help="Dernière copie à corriger")
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="Mode test (gratuit, sans appel LLM réel)",
    )
    parser.add_argument("--provider", default="ILAAS_OSS", help="Nom du provider LLM")

    args = parser.parse_args()

    logger.info(f"Début de la correction incrémentale pour {args.project_dir}")
    logger.info(f"Copies : {args.start} à {args.end}")
    logger.info(f"Mode : {'TEST (mock)' if args.test_mode else 'REEL (Qwen)'}")
    logger.info("-" * 60)

    correct_all_incremental(
        start=args.start,
        end=args.end,
        project_dir=args.project_dir,
        provider_name=args.provider,
        test_mode=args.test_mode,
    )

    logger.info("-" * 60)
    logger.info("Correction terminée !")


if __name__ == "__main__":
    main()
