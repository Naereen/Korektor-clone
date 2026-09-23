
import os
import argparse
import csv
import json
from llm_providers import get_provider, ask_llm

BASE_DIR = "copies_anonymes_validees"
BAREME_FILE = "bareme.csv"

def load_bareme(project_dir="."):
    """Load the bareme CSV file."""
    path = os.path.join(project_dir, BAREME_FILE)
    if not os.path.exists(path):
        print(f"Error: {path} not found.")
        return []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)

def correct_copy(copy_number, provider_name="ILAAS_OSS", bareme_data=None, force=False, project_dir=".", stop_callback=None, thinking=False):
    """Correct a single copy using the bareme data and LLM."""
    if stop_callback and stop_callback():
        print("Opération annulée (Start).")
        return False

    if bareme_data is None or not bareme_data:
        print("Error: Bareme data is missing or empty.")
        return False

    base_dir = os.path.join(project_dir, BASE_DIR)

    copy_dir_name = f"Copie_{copy_number:02d}"
    output_dir = os.path.join(base_dir, copy_dir_name)
    extraction_file = os.path.join(output_dir, "extraction.md")
    correction_csv_file = os.path.join(output_dir, "correction.csv")

    if not os.path.exists(extraction_file):
        print(f"Skipping {copy_dir_name}: No extraction.md found.")
        return False

    if os.path.exists(correction_csv_file) and not force:
         print(f"Skipping {copy_dir_name}: {correction_csv_file} already exists.")
         return True

    print(f"Correcting {copy_dir_name} using {provider_name} to {correction_csv_file}...")

    try:
        if stop_callback and stop_callback():
             print("Opération annulée (Pre-LLM).")
             return False

        # Load contents
        sujet_path = os.path.join(project_dir, "sujet.md")
        corrige_path = os.path.join(project_dir, "corrige.md")

        with open(sujet_path, "r", encoding="utf-8") as f:
            sujet_content = f.read()
        with open(corrige_path, "r", encoding="utf-8") as f:
            corrige_content = f.read()
        with open(extraction_file, "r", encoding="utf-8") as f:
            copie_content = f.read()

        # Build dynamic instructions from bareme
        questions_instructions = ""
        for row in bareme_data:
            q_id = row['question']
            q_type = row['qcm (oui/non)']

            questions_instructions += f"- Question {q_id} ({'QCM' if q_type == 'oui' else 'Ouverte'}):\n"
            if q_type == 'oui':
                questions_instructions += f"  Réponse correcte attendue : {row['réponse attendue']}\n"
                questions_instructions += "  Barème : +1 pt par bonne réponse, -0.5 pt par mauvaise, +1 rédaction.\n"
            else:
                questions_instructions += f"  Réponse attendue : {row['réponse attendue']}\n"
                questions_instructions += f"  Points : {row['points']}\n"

        prompt_text = (
            "Tu es un correcteur automatique. Ton objectif est de noter une copie d'étudiant selon un barème précis.\n"
            "Voici les documents :\n\n"
            f"<sujet>\n{sujet_content}\n</sujet>\n\n"
            f"<corrige>\n{corrige_content}\n</corrige>\n\n"
            f"<copie_etudiant>\n{copie_content}\n</copie_etudiant>\n\n"
            "INSTRUCTIONS DE CORRECTION :\n"
            "Pour chaque question listée ci-dessous, analyse la réponse de l'étudiant et attribue une note et un commentaire.\n"
            "IMPORTANT: 'reponse_etudiant' et 'commentaire' sont OBLIGATOIRES pour chaque question.\n"
            "Suis PRÉCISÉMENT le corrigé pour assurer une correction uniforme de toutes les copies.\n"
            "ATTENTION: Pour le LaTeX, DOUBLE-ÉCHAPPE tous les backslashes dans le JSON. Exemple: écris '\\\\sin' au lieu de '\\sin', '\\\\frac' au lieu de '\\frac'.\n"
            f"{questions_instructions}\n\n"
            "FORMAT DE SORTIE ATTENDU :\n"
            "Tu dois répondre UNIQUEMENT avec un objet JSON valide. Pas de texte avant ou après.\n"
            "Le JSON doit être un dictionnaire où les clés sont les identifiants exacts des questions (ex: 'Q0', 'Q1.1', 'Q_part2.1a') "
            "et les valeurs sont des objets contenant OBLIGATOIREMENT:\n"
            "- 'reponse_etudiant': (string) la réponse EXACTE de l'étudiant (copie-colle du texte de la copie)\n"
            "- 'note': (number) la note finale attribuée pour la question\n"
            "- 'commentaire': (string) justification courte mais explicative (minimum 10 caractères)\n"
            "Champs OPTIONNELS pour QCM:\n"
            "- 'nb_bonnes': (number) nombre de réponses justes cochées\n"
            "- 'nb_mauvaises': (number) nombre de réponses fausses cochées\n"
            "- 'bonus_redaction': (number) points de rédaction (0 ou 1)\n"
            "\n"
            "REGLES:\n"
            "- Si la question n'a pas de réponse, mets reponse_etudiant: \"\" et commentaire: \"Pas de réponse\"\n"
            "- Si tu ne comprends pas la réponse, décris ce que tu vois dans reponse_etudiant\n"
            "\n"
            "Exemple JSON:\n"
            "{\n"
            '  "Q0": { "reponse_etudiant": "Affiche 0. Après un std::move, s est vide.", "note": 2, "commentaire": "Tout juste. L\'étudiant a bien compris le move semantics." },\n'
            '  "Q1.1": { "reponse_etudiant": "D", "note": 1, "commentaire": "Bonne réponse: new int[10] alloue un tableau de 10 entiers." },\n'
            '  "Q_part2.1a": { "reponse_etudiant": "case \\\'+\\\': return lval + rval;", "note": 3, "commentaire": "Code correct pour l\'addition." }\n'
            "}\n"
        )

        system_prompt = (
            "Tu es un assistant JSON strict. Tu ne parles que JSON.\n"
            "ATTENTION: Le contenu contient du LaTeX. Tu DOIS échapper les backslashes dans les chaînes JSON.\n"
            "Exemple: au lieu de \"\\frac\", écris \"\\\\frac\". Au lieu de \"\\mu\", écris \"\\\\mu\"."
        )

        provider = get_provider(provider_name)
        response = ask_llm(provider, prompt_text, system_prompt=system_prompt, thinking=thinking)

        # DEBUG: Save raw response to file for analysis
        with open('/tmp/llm_raw_response_copie02.txt', 'w', encoding='utf-8') as f:
            f.write(response)
        print(f"DEBUG: Raw response saved to /tmp/llm_raw_response_copie02.txt")

        if stop_callback and stop_callback():
             print("Opération annulée (Post-LLM - Sauvegarde ignorée).")
             return False

        # Use json_repair to extract and parse JSON from response (handles markdown blocks, LaTeX, etc.)
        try:
            import json_repair
            correction_data = json_repair.loads(response)
        except Exception as e:
            print(f"Erreur lors du parsing JSON (même avec json_repair): {e}")
            print(f"Réponse brute : {response[:500]}...")
            return False

        # Merge with bareme data and write to CSV
        fieldnames = list(bareme_data[0].keys()) # Start with bareme columns
        # Add extra fields from LLM correction
        extra_fields = ['réponses étudiant', 'commentaires éventuels du correcteur', 'nbre de bonnes réponses si QCM', 'nbre de mauvaises réponses si QCM', 'Rédaction si QCM']
        for f in extra_fields:
            if f not in fieldnames:
                fieldnames.append(f)

        with open(correction_csv_file, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for row in bareme_data:
                q_id = row['question']
                # Copy row to avoid modifying the original list in memory across iterations
                new_row = row.copy()

                if q_id in correction_data:
                    c_data = correction_data[q_id]
                    # Fill processed fields
                    new_row['réponses étudiant'] = c_data.get('reponse_etudiant', '')
                    new_row['note'] = str(c_data.get('note', ''))
                    new_row['commentaires éventuels du correcteur'] = c_data.get('commentaire', '')

                    # Fill QCM specific fields and recalculate note if present
                    if row['qcm (oui/non)'] == 'oui':
                        try:
                            nb_bonnes = float(c_data.get('nb_bonnes', 0))
                            nb_mauvaises = float(c_data.get('nb_mauvaises', 0))
                            bonus_redaction = float(c_data.get('bonus_redaction', 0))

                            new_row['nbre de bonnes réponses si QCM'] = str(nb_bonnes)
                            new_row['nbre de mauvaises réponses si QCM'] = str(nb_mauvaises)
                            new_row['Rédaction si QCM'] = str(bonus_redaction)

                            # Recalculate note to ensure consistency
                            # Barème : +1 par bonne, -0.5 par mauvaise, +1 rédaction
                            # On suppose que la note minimale d'une question est 0
                            raw_score = (nb_bonnes * 1.0) - (nb_mauvaises * 0.5)
                            # computed_note = max(0, raw_score) + bonus_redaction # Hypothèse standard: le négatif n'est pas conservé avant bonus
                            # Ou formule simple brute :
                            computed_note = max(0, raw_score + bonus_redaction)

                            new_row['note'] = str(computed_note)
                        except ValueError:
                            # Fallback to LLM note if parsing fails
                            pass

                else:
                    new_row['commentaires éventuels du correcteur'] = "NON NOTÉ (Erreur LLM ou ID non trouvé)"

                writer.writerow(new_row)

        print(f"Success! Correction saved to {correction_csv_file}")
        return True

    except Exception as e:
        print(f"Error correcting {copy_dir_name}: {e}")
        return False

def get_existing_copies(project_dir="."):
    """Return a sorted list of copy numbers found in BASE_DIR."""
    base_dir_path = os.path.join(project_dir, BASE_DIR)

    if not os.path.exists(base_dir_path):
        return []

    copies = []
    for entry in os.listdir(base_dir_path):
        if entry.startswith("Copie_") and os.path.isdir(os.path.join(base_dir_path, entry)):
            try:
                num = int(entry.split("_")[1])
                copies.append(num)
            except ValueError:
                continue
    return sorted(copies)

def main():
    parser = argparse.ArgumentParser(description="Batch correct exam copies.")
    parser.add_argument("--start", type=int, default=None, help="Start copy number (inclusive)")
    parser.add_argument("--end", type=int, default=None, help="End copy number (inclusive)")
    parser.add_argument("--provider", type=str, default="ILAAS_OSS", help="LLM Provider to use")
    parser.add_argument("--force", action="store_true", help="Force overwrite of existing correction files")
    parser.add_argument("--project-dir", type=str, default=".", help="Project directory")
    parser.add_argument("--no-thinking", action="store_true", help="Disable thinking mode for LLM (useful for Qwen models)")

    args = parser.parse_args()

    bareme_data = load_bareme(args.project_dir)
    print(f"Loaded bareme with {len(bareme_data)} questions.")

    if not bareme_data:
        return

    existing_copies = get_existing_copies(args.project_dir)
    base_dir_path = os.path.join(args.project_dir, BASE_DIR)

    if not existing_copies:
        print(f"No copies found in {base_dir_path}.")
        return

    # Determine range
    start_num = args.start if args.start is not None else existing_copies[0]
    end_num = args.end if args.end is not None else existing_copies[-1]

    # Filter copies to process based on range
    copies_to_process = [c for c in existing_copies if start_num <= c <= end_num]

    print(f"Found {len(existing_copies)} copies total in {base_dir_path}.")
    print(f"Processing {len(copies_to_process)} copies (from {start_num} to {end_num}) using {args.provider}...")

    for i in copies_to_process:
        correct_copy(i, args.provider, bareme_data, force=args.force, project_dir=args.project_dir, thinking=False)

    print("Batch correction complete.")

if __name__ == "__main__":
    main()
