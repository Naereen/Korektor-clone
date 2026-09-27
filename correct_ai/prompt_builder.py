"""
Module de construction des prompts incrémentaux pour Korektor.

Ce module gère la création des prompts optimisés pour le LLM, incluant :
- Le sujet et le corrigé
- Le barème complet
- Les corrections des copies précédentes (pour uniformité)
- La copie courante à corriger

Utilisation:
    from correct_ai.prompt_builder import build_incremental_prompt

    prompt = build_incremental_prompt(
        sujet=sujet_content,
        corrige=corrige_content,
        bareme=bareme_list,
        previous_corrections=prev_corrections,
        current_pdf_path="Copie_03.pdf",
        current_extraction=extraction_text
    )
"""

import json
from typing import Any, Dict, List


def _output_question_number(question: Any) -> Any:
    """Use numeric identifiers when possible while preserving composite labels."""
    text = str(question).strip()
    suffix = text[1:] if text.upper().startswith("Q") else text
    return int(suffix) if suffix.isdigit() else text


def _qwen_output_template(bareme: List[Dict[str, Any]]) -> str:
    """Build the exact structured output expected from Qwen."""
    payload = {
        "questions": [
            {
                "num_question": _output_question_number(question["question"]),
                "note": 0.0,
                "bareme": float(question["points"]),
                "reponse_etudiant": "",
                "commentaire": "Non répondu.",
                "page": None,
                "bbox_2d": None,
            }
            for question in bareme
        ]
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_incremental_prompt(
    sujet: str,
    corrige: str,
    bareme: List[Dict[str, Any]],
    previous_corrections: List[Dict[str, Any]],
    current_pdf_path: str,
    current_extraction: str,
) -> str:
    """
    Construit le prompt optimisé pour le LLM avec approche incrémentale.

    Le prompt inclut :
    1. Contexte global (sujet, corrigé, barème)
    2. Corrections des copies précédentes (pour uniformité)
    3. Copie courante à corriger (PDF + extraction OCR)
    4. Instructions strictes

    Args:
        sujet: Contenu complet de sujet.md
        corrige: Contenu complet de corrige.md
        bareme: Liste des questions du barème (dicts avec id, question, texte_question, type_qcm, reponse_attendue, points)
        previous_corrections: Liste des corrections des copies 1..n-1
        current_pdf_path: Chemin vers le PDF de la copie courante
        current_extraction: Texte OCR de la copie courante

    Returns:
        str: Prompt complet prêt à être envoyé au LLM
    """
    # Section 1: Introduction et contexte global
    prompt = """Tu es un correcteur expert, en informatique et en mathématiques, en CPGE filière MP2I en France. Ton rôle est de noter les copies d'examen avec JUSTICE et UNIFORMITÉ. Corrige cette copie selon les pièces jointes : le sujet, le corrigé et le barème.

---
# CONTEXTE GLOBAL (à utiliser pour référence uniquement)
---

## SUJET:
{sujet}

## CORRIGÉ OFFICIEL:
{corrige}

## BAREME:
""".format(
        sujet=sujet, corrige=corrige
    )

    # Ajouter chaque question du barème
    for q in bareme:
        q_type = "QCM" if q["type_qcm"] else "Ouverte"
        q_text = q["texte_question"] if q["texte_question"] else q["question"]
        prompt += f"- **{q['question']}** ({q_type}, {q['points']} pts): {q_text}\n"
        prompt += f"  → Réponse attendue: {q['reponse_attendue']}\n"

    # Section 2: Corrections précédentes (pour uniformité)
    if previous_corrections:
        prompt += """
---
# CORRECTIONS PRÉCÉDENTES (pour garantir uniformité)
---
"""
        for corr in previous_corrections:
            prompt += f"\n### Copie {corr['copie_id']}:\n"
            raisonnement = getattr(corr, "raisonnement", "") or ""
            prompt += (
                f"- **{corr['question']}**: Note={corr['note']}, "
                f"Réponse='{corr['reponse_etudiant']}', "
                f"Commentaire='{corr['commentaire']}', "
                f"Raisonnement='{raisonnement}'\n"
            )

    # Section 3: Copie courante à corriger
    prompt += f"""
---
# COPIE COURANTE À CORRIGER
---
## Texte OCR:
{current_extraction}

## PDF: les pages de la copie sont fournies en images ci-dessous.

---
# FORMAT DE SORTIE — TEMPLATE JSON OBLIGATOIRE
---

Complète EXACTEMENT le template JSON ci-dessous. Tu ne dois NI supprimer NI ajouter de clés.
Remplace uniquement les valeurs de note, reponse_etudiant, commentaire, page et bbox_2d.

```json
"""
    prompt += _qwen_output_template(bareme)
    prompt += "\n```\n"

    prompt += """
---
# INSTRUCTIONS STRICTES:
1. Pour CHAQUE entrée de questions, remplace les valeurs par défaut par ta correction réelle.
2. Transcris fidèlement dans reponse_etudiant la réponse manuscrite de l'étudiant; si aucune réponse n'est visible, utilise une chaîne vide.
3. UTILISE les corrections précédentes comme REFÉRENCE pour maintenir la même sévérité.
4. Si un étudiant a écrit exactement la même chose qu'un précédent, donne la même note.
5. Le commentaire doit être assez détaillé pour l'étudiant, et les équations de maths seront entourées de $ ... $.
6. Si l'étudiant n'a pas répondu, conserve note=0.0, reponse_etudiant="", commentaire="Non répondu.", page=null et bbox_2d=null.
7. NE SUPPRIME ET N'AJOUTE AUCUNE CLÉ. Ne modifie pas num_question ni bareme.
8. page est le numéro de page du PDF, à partir de 1. Indique toujours cette page pour une bbox; ne la déduis pas du numéro de question. Si la réponse s'étend sur plusieurs pages ou si la page est incertaine, utilise page=null et bbox_2d=null.
9. bbox_2d vaut exactement [xmin, ymin, xmax, ymax], avec quatre entiers normalisés entre 0 et 1000 et l'origine en haut à gauche de la page indiquée. La boîte doit entourer l'encre manuscrite réellement visible de TOUTE la réponse, y compris les codes (réponse contenant de la programmation), et le numéro de question avant (et mieux vaut viser un peu plus, avec une boîte trop grande, que pas assez). Sa largeur et sa hauteur doivent chacune dépasser 20 unités. Si la localisation visuelle n'est pas certaine, utilise null.
"""

    return prompt


def build_simple_prompt(
    sujet: str,
    corrige: str,
    bareme: List[Dict[str, Any]],
    extraction: str,
    pdf_path: str,
) -> str:
    """
    Construit un prompt simple (sans contexte des corrections précédentes).

    Utilisé comme fallback ou pour la première copie.

    Args:
        sujet: Contenu du sujet
        corrige: Contenu du corrigé
        bareme: Liste des questions
        extraction: Texte OCR de la copie
        pdf_path: Chemin vers le PDF

    Returns:
        str: Prompt simple
    """
    prompt = """Tu es un correcteur expert en informatique et en mathématiques, en CPGE filière MP2I en France. Corrige cette copie selon les pièces jointes : le sujet, le corrigé et le barème.

---
# SUJET:
{sujet}

---
# CORRIGÉ:
{corrige}

---
# BAREME:
""".format(
        sujet=sujet, corrige=corrige
    )

    for q in bareme:
        q_type = "QCM" if q["type_qcm"] else "Ouverte"
        q_text = q["texte_question"] if q["texte_question"] else q["question"]
        prompt += f"- **{q['question']}** ({q_type}, {q['points']} pts): {q_text}\n"
        prompt += f"  → Réponse attendue: {q['reponse_attendue']}\n"

    prompt += f"""
---
# COPIE À CORRIGER:

## Texte OCR:
{extraction}

## PDF: les pages de la copie sont fournies en images ci-dessous.

---
# FORMAT DE SORTIE — TEMPLATE JSON OBLIGATOIRE
---

Complète EXACTEMENT le template JSON ci-dessous. Ne supprime ni n'ajoute de clés.

```json
"""
    prompt += _qwen_output_template(bareme)
    prompt += "\n```\n"

    prompt += """
---
# INSTRUCTIONS:
1. Pour chaque entrée de questions, remplace les valeurs par défaut.
2. Si l'étudiant n'a pas répondu, conserve note=0.0, commentaire="Non répondu.", page=null et bbox_2d=null.
3. Le commentaire doit être assez détaillé pour l'étudiant, et les équations de maths seront entourées de $ ... $.
4. NE SUPPRIME ET N'AJOUTE AUCUNE CLÉ. Ne modifie pas num_question ni bareme.
5. page est le numéro de page du PDF, à partir de 1. Indique toujours cette page pour une bbox; ne la déduis pas du numéro de question. Si la réponse s'étend sur plusieurs pages ou si la page est incertaine, utilise page=null et bbox_2d=null.
6. bbox_2d vaut exactement [xmin, ymin, xmax, ymax], avec quatre entiers normalisés entre 0 et 1000 et l'origine en haut à gauche de la page indiquée. La boîte doit entourer l'encre manuscrite réellement visible de TOUTE la réponse (et mieux vaut viser un peu plus, avec une boîte trop grande, que pas assez). Sa largeur et sa hauteur doivent chacune dépasser 20 unités. Si la localisation visuelle n'est pas certaine, utilise null.
"""

    return prompt


def build_bbox_refinement_prompt(
    question_label: str,
    question_text: str,
    page_number: int,
    response_text: str,
    page_ocr_text: str,
) -> str:
    """Build a focused prompt for refining a candidate answer box in one crop."""
    return f"""Localise précisément dans l'image fournie l'encre manuscrite de la réponse à {question_label}.
L'image est un recadrage haute résolution de la page {page_number}; toutes les coordonnées demandées sont relatives à CE recadrage, pas à la page complète.

Énoncé de la question : {question_text or question_label}
Transcription OCR attendue de la réponse : {response_text or "non fournie"}
Texte OCR de la page pour contexte :
{page_ocr_text or "non disponible"}

Retourne uniquement un objet JSON de la forme {{"bbox_2d": [xmin, ymin, xmax, ymax]}} avec des entiers normalisés de 0 à 1000 et l'origine en haut à gauche du crop. La boîte doit couvrir toute l'encre manuscrite correspondant à cette réponse, sans inclure les réponses voisines. Si la réponse ne peut pas être identifiée avec confiance, retourne {{"bbox_2d": null}}.
"""


def estimate_prompt_tokens(
    sujet: str,
    corrige: str,
    bareme: List[Dict[str, Any]],
    previous_corrections: List[Dict[str, Any]],
    current_extraction: str,
) -> int:
    """
    Estime le nombre de tokens du prompt.

    Args:
        sujet: Contenu du sujet
        corrige: Contenu du corrigé
        bareme: Liste des questions
        previous_corrections: Liste des corrections précédentes
        current_extraction: Texte OCR de la copie courante

    Returns:
        int: Nombre estimé de tokens
    """
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
    except ImportError:
        # Fallback : estimation grossière (1 token ≈ 4 caractères)
        total_chars = len(sujet) + len(corrige) + len(current_extraction)
        for q in bareme:
            total_chars += len(q["texte_question"] or q["question"] or "")
        for corr in previous_corrections:
            total_chars += len(str(corr))
        return total_chars // 4

    # Construire le prompt et compter les tokens
    prompt = build_incremental_prompt(
        sujet=sujet,
        corrige=corrige,
        bareme=bareme,
        previous_corrections=previous_corrections,
        current_pdf_path="dummy.pdf",
        current_extraction=current_extraction,
    )
    return len(enc.encode(prompt))
