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

from typing import Any, Dict, List


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
    prompt = """Tu es un correcteur expert, en informatique et en mathématiques, en CPGE filière MP2I en France. Ton rôle est de noter les copies d'examen avec JUSTICE et UNIFORMITÉ.

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
Remplace uniquement les valeurs : note, commentaire, raisonnement, reponse_etudiant, bounding_boxes.

```json
{{
"""
    # Générer le template avec toutes les questions du barème
    template_entries = []
    for q in bareme:
        template_entries.append(
            f'    "{q["question"]}": {{'
            f'"note": 0.0, '
            f'"commentaire": "Non répondu.", '
            f'"raisonnement": "", '
            f'"reponse_etudiant": "", '
            f'"bounding_boxes": []'
            f"}}"
        )
    prompt += ",\n".join(template_entries)
    prompt += "\n}}\n```\n"

    prompt += """
---
# INSTRUCTIONS STRICTES:
1. Pour CHAQUE question du template, remplace les valeurs par défaut par ta correction réelle.
2. UTILISE les corrections précédentes comme REFÉRENCE pour maintenir la même sévérité.
3. Si un étudiant a écrit exactement la même chose qu'un précédent, donne la même note.
4. Le commentaire doit être super long, mais assez détaillé quand-même (il sera donné tel quel à l'étudiant), et les équations de maths seront entourées de $ ... $. Le raisonnement peut être plus détaillé que le commentaire (il sera uniquement lu par le professeur).
5. Si l'étudiant n'a pas répondu à une question, laisse les valeurs par défaut (note=0.0, commentaire="Non répondu.") et explique dans le raisonnement ce qui était attendu.
6. NE SUPPRIME AUCUNE CLÉ du template. Ne modifie pas les noms des questions.
7. Pour chaque question, remplis "bounding_boxes" avec les zones des erreurs ou remarques visibles sur la copie :
    "bounding_boxes": [{"box_2d": [ymin, xmin, ymax, xmax], "label": "Description synthétique de l'erreur ou remarque"}]
    Les coordonnées ymin, xmin, ymax, xmax sont des entiers normalisés entre 0 et 1000 par rapport aux dimensions de l'image de la page (origine en haut à gauche). Utilise [] si aucune zone n'est identifiable.
    La boîte doit entourer l'encre manuscrite réellement visible pour la question, pas une bande étroite ni une position déduite du seul texte OCR. Vérifie sur l'image que la boîte couvre la réponse et laisse une marge suffisante pour la lire (au moins 80 unités normalisées en hauteur pour une réponse sur une ligne, davantage pour plusieurs lignes).
    Si la copie a plusieurs pages, ajoute "page": 1 (numéro de page à partir de 1) dans chaque objet de bounding_boxes ; les coordonnées sont relatives à cette page.
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
    prompt = """Tu es un correcteur expert. Corrige cette copie selon le sujet, le corrigé et le barème.

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
{{
"""
    template_entries = []
    for q in bareme:
        template_entries.append(
            f'    "{q["question"]}": {{'
            f'"note": 0.0, '
            f'"commentaire": "Non répondu.", '
            f'"raisonnement": "", '
            f'"reponse_etudiant": "", '
            f'"bounding_boxes": []'
            f"}}"
        )
    prompt += ",\n".join(template_entries)
    prompt += "\n}}\n```\n"

    prompt += """
---
# INSTRUCTIONS:
1. Pour chaque question du template, remplace les valeurs par défaut.
2. Si l'étudiant n'a pas répondu, laisse les valeurs par défaut et explique dans le raisonnement ce qui était attendu.
3. Le commentaire doit être pas trop long (il sera donné tel quel à l'étudiant), et les équations de maths seront entourées de $ ... $. Le raisonnement doit être plus détaillé que le commentaire (il sera uniquement lu par le professeur).
4. NE SUPPRIME AUCUNE CLÉ du template.
5. Pour chaque question, fournis "bounding_boxes": [{"box_2d": [ymin, xmin, ymax, xmax], "label": "Description synthétique de l'erreur ou remarque"}], avec des coordonnées entières normalisées entre 0 et 1000 par rapport à l'image de la page. Utilise [] si aucune zone n'est identifiable et ajoute "page": XXX, dans chaque boîte pour une copie multipage.
    La boîte doit entourer l'encre manuscrite réellement visible pour la question, pas une bande étroite ni une position déduite du seul texte OCR. Vérifie sur l'image que la boîte couvre la réponse et laisse une marge suffisante pour la lire (au moins 80 unités normalisées en hauteur pour une réponse sur une ligne, davantage pour plusieurs lignes).
"""

    return prompt


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
