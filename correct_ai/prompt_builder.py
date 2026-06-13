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
from typing import List, Dict, Any


def build_incremental_prompt(
    sujet: str,
    corrige: str,
    bareme: List[Dict[str, Any]],
    previous_corrections: List[Dict[str, Any]],
    current_pdf_path: str,
    current_extraction: str
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
    prompt = """Tu es un correcteur expert. Ton rôle est de noter les copies d'examen 
avec JUSTICE et UNIFORMITÉ.

---
# CONTEXTE GLOBAL (à utiliser pour référence uniquement)
---

## SUJET:
{sujet}

## CORRIGÉ OFFICIEL:
{corrige}

## BAREME:
""".format(sujet=sujet, corrige=corrige)
    
    # Ajouter chaque question du barème
    for q in bareme:
        q_type = 'QCM' if q['type_qcm'] else 'Ouverte'
        q_text = q['texte_question'] if q['texte_question'] else q['question']
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
            raisonnement = getattr(corr, 'raisonnement', '') or ''
            prompt += f"- **{corr['question']}**: Note={corr['note']}, " \
                      f"Réponse='{corr['reponse_etudiant']}', " \
                      f"Commentaire='{corr['commentaire']}', " \
                      f"Raisonnement='{raisonnement}'\n"
    
    # Section 3: Copie courante à corriger
    prompt += f"""
---
# COPIE COURANTE À CORRIGER
---
## Texte OCR:
{current_extraction}

## PDF: [image de {current_pdf_path}]

---
# INSTRUCTIONS STRICTES:
1. Pour chaque question du barème, attribue une note, un commentaire, et une explication détaillée (raisonnement).
2. UTILISE les corrections précédentes comme REFÉRENCE pour maintenir la même sévérité.
3. Si un étudiant a écrit exactement la même chose qu'un précédent, donne la même note.
4. Le commentaire doit être concis (pour l'étudiant), le raisonnement doit être plus détaillé que le commentaire et expliquer le processus qui a conduit à la note et au commentaire. Par exemple en s'appuyant sur le corrigé et des comparaisons avec certaines copies déjà corrigées.
5. Format de sortie: JSON avec {{question: {{note: ..., commentaire: ..., raisonnement: ..., reponse_etudiant: ...}}}}
"""
    
    return prompt


def build_simple_prompt(
    sujet: str,
    corrige: str,
    bareme: List[Dict[str, Any]],
    extraction: str,
    pdf_path: str
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
""".format(sujet=sujet, corrige=corrige)
    
    for q in bareme:
        q_type = 'QCM' if q['type_qcm'] else 'Ouverte'
        q_text = q['texte_question'] if q['texte_question'] else q['question']
        prompt += f"- **{q['question']}** ({q_type}, {q['points']} pts): {q_text}\n"
        prompt += f"  → Réponse attendue: {q['reponse_attendue']}\n"
    
    prompt += f"""
---
# COPIE À CORRIGER:

## Texte OCR:
{extraction}

## PDF: [image de {pdf_path}]

---
# INSTRUCTIONS:
Corrige chaque question avec note, commentaire concis et raisonnement détaillé. Format de sortie: JSON avec {{question: {{note: ..., commentaire: ..., raisonnement: ...}}}}
"""
    
    return prompt


def estimate_prompt_tokens(
    sujet: str,
    corrige: str,
    bareme: List[Dict[str, Any]],
    previous_corrections: List[Dict[str, Any]],
    current_extraction: str
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
            total_chars += len(q.get('texte_question', q.get('question', '')))
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
        current_extraction=current_extraction
    )
    return len(enc.encode(prompt))
