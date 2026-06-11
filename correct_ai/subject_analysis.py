
import os
import sys
import json
import argparse
import re
import csv
from llm_providers import get_provider, ask_llm

def analyze_subject(project_dir=".", output_file="structure.json"):
    sujet_path = os.path.join(project_dir, "sujet.md")
    
    if not os.path.exists(sujet_path):
        print(f"Error: {sujet_path} not found.")
        return False

    with open(sujet_path, 'r', encoding='utf-8') as f:
        original_content = f.read()

    print(f"Analyzing {sujet_path} ({len(original_content)} chars)...")
    print("Using REGEX extraction based on annotations (--Qx.y)...")

    # Regex to find markers like (--Q1.1) or (--Q1) or (--Q_Intro)
    # Captures the marker content e.g. "Q1.1"
    pattern = r'\(--(Q[\w\.]+)\)'
    
    # Split: this returns [TextBefore, Marker1, TextBetween, Marker2, ...]
    parts = re.split(pattern, original_content)
    
    # parts[0] is text before first marker.
    # parts[1] is first marker ID (e.g. Q1.0)
    # parts[2] is text between first and second...
    
    data = []
    
    # We expect an odd number of parts if the file ends with text (but usually ends with newline)
    # Reconstruct logic:
    # Item 0: Text -> assigned to Marker 0 (parts[1])
    # Item i: Text -> assigned to Marker i...
    
    if len(parts) < 2:
        print("Error: No markers (--Qx.y) found in subject.")
        return False
        
    current_text = parts[0]
    
    # Iterate over pairs (Marker, NextText)
    # parts[1] is marker, parts[2] is text after marker... 
    # WAIT. 
    # User annotation: "Question Text... (--Q1.0)"
    # So "Question Text..." belongs to Q1.0. 
    # The split puts "Question Text..." in parts[0], and "Q1.0" in parts[1].
    # Then parts[2] is the text *following* Q1.0, which belongs to Q1.1 (parts[3]).
    
    # So the loop should pair parts[i] (text) with parts[i+1] (id).
    
    for i in range(0, len(parts)-1, 2):
        text_content = parts[i]
        question_id = parts[i+1]
        
        # Check if text_content is just whitespace? No, context matters.
        
        # We store the text *including* the definition of the question.
        # But we remove the marker from the text field (it is the ID).
        # Reconstruction will be: text + "(--" + numero + ")"
        
        data.append({
            "numero": question_id,
            "texte": text_content
        })
        
    # Handle any trailing text after the last marker?
    # annotated file ends with (--Q4.2)\n
    # So parts[-1] should be just whitespace or empty.
    last_bit = parts[-1]
    if last_bit.strip():
         print(f"Warning: Text found after last marker: '{last_bit[:50]}...'")
         # We might need a dummy section or attach to previous?
         # Ideally the file ends with a marker.
         # Let's attach it to a "Footer" or similar if critical.
         # Or assuming prompt says "Full reconstruction", we must save it.
         data.append({
             "numero": "END", # Special marker
             "texte": last_bit
         })
    elif last_bit:
         # Just whitespace
         data.append({
             "numero": "END", 
             "texte": last_bit
         })

    # VERIFICATION
    reconstructed = ""
    for item in data:
        reconstructed += item['texte']
        if item['numero'] != "END":
            reconstructed += f"(--{item['numero']})"
            
    if reconstructed == original_content:
        print("✅ SUCCÈS : La reconstruction est PARFAITE (byte-exacte).")
    else:
        print("⚠️ ATTENTION : Différences détectées (Regex reconstruction).")
        print(f"Original len: {len(original_content)}")
        print(f"Reconstructed len: {len(reconstructed)}")
        # Debug diff
        for i in range(min(len(reconstructed), len(original_content))):
             if reconstructed[i] != original_content[i]:
                 print(f"Diff at {i}: Orig '{original_content[i]}' vs Rec '{reconstructed[i]}'")
                 print(f"Context: {original_content[max(0, i-20):i+20]}")
                 break
                 
    output_path = os.path.join(project_dir, output_file)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        
    print(f"Structure saved to {output_path}")
    
    # Generate Bareme CSV immediately
    generate_bareme_from_structure(data, project_dir)
    return True

def generate_bareme_from_structure(structure_data, project_dir="."):
    """
    Generates bareme.csv based on the structure.json.
    Uses LLM to guess if QCM and expected answer.
    """
    bareme_path = os.path.join(project_dir, "bareme.csv")
    print("Generating bareme.csv from structure...")
    
    # Load corrige for accurate answers
    corrige_path = os.path.join(project_dir, "corrige.md")
    with open(corrige_path, "r", encoding="utf-8") as f:
        corrige_content = f.read()
    
    # Prepare data for LLM analysis
    # We send the list of questions and ask for a JSON enrichment
    # Chunking might be needed if too many questions, but usually fits in context (100 lines json)
    
    questions_text = json.dumps([{k:v for k,v in item.items() if k in ['numero', 'texte']} for item in structure_data], ensure_ascii=False)
    
    prompt = (
        "Tu es un assistant pédagogique.\n"
        "Voici la liste des questions d'un examen (format JSON).\n"
        "Voici aussi le corrigé officiel :\n"
        f"<corrige>\n{corrige_content}\n</corrige>\n\n"
        "Pour chaque question, tu dois déterminer :\n"
        "1. Est-ce un QCM ? (Oui/Non)\n"
        "2. Quelle est la réponse attendue (si QCM, indique la bonne case, sinon résume l'attendu).\n"
        "3. Le barème suggéré (points).\n"
        "Suis PRÉCISÉMENT le corrigé officiel pour les réponses attendues.\n"
        "\n"
        f"DATA: {questions_text}\n"
        "\n"
        "FORMAT DE REPONSE (JSON strict) :\n"
        "Une liste d'objets : `[{\"numero\": \"Qx.y\", \"qcm\": \"Oui/Non\", \"reponse\": \"...\", \"points\": \"...\"}, ...]`\n"
        "Réponds UNIQUEMENT le JSON."
    )
    
    try:
        # Use UNISTRA_QWEN for consistency
        provider = get_provider("UNISTRA_QWEN")
        
        response = ask_llm(provider, prompt, system_prompt="Tu es un expert en évaluation.", thinking=False)
        
        cleaned = re.sub(r'^```json\s*', '', response.strip())
        cleaned = re.sub(r'\s*```$', '', cleaned)
        enrichment = json.loads(cleaned)
        
        # Convert to Dict for easy lookup
        enrich_map = {item['numero']: item for item in enrichment}
        
        fieldnames = ["question", "qcm (oui/non)", "réponse attendue", "points", "note", "commentaires"]
        
        with open(bareme_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for item in structure_data:
                qid = item['numero']
                if qid == "END": continue
                
                info = enrich_map.get(qid, {})
                writer.writerow({
                    "question": qid,
                    "qcm (oui/non)": info.get('qcm', 'Non'),
                    "réponse attendue": info.get('reponse', ''),
                    "points": info.get('points', ''),
                    "note": "",
                    "commentaires": ""
                })
        print(f"Barelme saved to {bareme_path}")
        
    except Exception as e:
        print(f"Error generating bareme: {e}")
        # Fallback: create empty bareme
        fieldnames = ["question", "qcm (oui/non)", "réponse attendue", "points"]
        with open(bareme_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for item in structure_data:
                 if item['numero'] == "END": continue
                 writer.writerow({"question": item['numero'], "qcm (oui/non)": "?", "réponse attendue": "", "points": ""})

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze exam subject structure")
    parser.add_argument("--project-dir", default=".", help="Project directory containing sujet.md")
    args = parser.parse_args()
    
    analyze_subject(args.project_dir)
