# Korektor - Correction Automatisée de Copies Manuscrites

<table>
  <tr>
    <td width="60%" valign="middle">
      <p>Korektor est une application Web (Flask) conçue pour automatiser le flux de correction de copies d'examen manuscrites en utilisant des modèles de langage (LLM).</p>
      <p><strong>Fonctionnalités :</strong></p>
      <ol>
        <li><strong>Numérisation et Import</strong> : Découpage intelligent de scans (A3 -> A4) et regroupement par étudiant.</li>
        <li><strong>Extraction (OCR)</strong> : Transcription du contenu manuscrit en Markdown avec structuration automatique (questions, réponses).</li>
        <li><strong>Correction IA</strong> : Notation automatique basée sur un barème précis (<code>bareme.csv</code>), générant des notes et des commentaires.</li>
        <li><strong>Vérification Professeur</strong> : Interface ergonomique pour relire les copies, ajuster les notes et annoter.</li>
      </ol>
    </td>
    <td width="30%" valign="middle">
      <img src="korektor.jpeg" alt="Korektor" width="100%">
    </td>
  </tr>
</table>

## Installation

1.  **Prérequis** : Python 3.8+
2.  **Créer un environnement virtuel** :
    ```bash
    uv venv  # ou python -m venv .venv
    source .venv/bin/activate  # Sur Linux/Mac
    # .venv\Scripts\activate   # Sur Windows
    ```
3.  **Installer les dépendances** :
    ```bash
    uv pip install -r requirements.txt # ou pip install -r requirements.txt
    pip install json_repair  # Pour réparer les erreurs JSON LaTeX
    ```

## Utilisation

1.  **Lancer l'application** :
    ```bash
    python correct_ai/app.py
    ```
    L'interface est accessible sur [http://127.0.0.1:5000](http://127.0.0.1:5000).

2.  **Configurer le projet** :
    *   L'application travaille par défaut dans le dossier `./exemple`.
    *   Vous pouvez changer de dossier via l'interface "Paramètres".
    *   Assurez-vous d'avoir vos fichiers de contexte (`sujet.md`, `corrige.md`, `bareme.csv`, `liste-etudiants.csv`).

3.  **Workflow** :
    *   **Découpage** : Importez vos PDF (A3 ou A4) et validez le découpage et l'identification des étudiants.
    *   **Traitement** (Batch) : Lancez l'extraction (OCR) puis la correction pour tout ou partie des copies.
    *   **Vérification** : Passez en revue chaque copie. L'interface affiche le PDF à gauche et le formulaire de notation à droite.
        *   Modifiez les notes ou commentaires si nécessaire.
        *   Cliquez sur "Enregistrer Tout". Vos modifications sont sauvées dans `correction_prof.csv`.

## Structure des Fichiers de Données

Pour chaque copie (ex: `Copie_01`), le système génère :
*   `Copie_01.pdf` : Le scan individuel.
*   `extraction.md` : Le contenu transcrit.
*   `correction.csv` : La correction proposée par l'IA.
*   `correction_prof.csv` : La correction validée/modifiée par l'enseignant (prioritaire).

## Configuration API

Les clés API pour les LLM (Mistral, OpenAI, etc.) doivent être définies dans vos variables d'environnement ou configurées via la classe `ProviderConfig` dans `correct_ai/llm_providers.py`.

```bash
export MISTRAL_API_KEY="votre_cle_ici"
```

---

## Configuration Recommandée

### Provider par défaut
- **UNISTRA_QWEN** (Qwen3) avec `thinking=True` : Le plus fiable pour la correction.
- Alternative : **PALGANIA_QWEN3** (nécessite `json_repair` pour les erreurs LaTeX).

### Paramètres clés
- **Timeout** : 600 secondes (toutes les requêtes)
- **Thinking** : Activé par défaut (`chat_template_kwargs: {"enable_thinking": true}`)
- **Parsing JSON** : `json_repair` utilisé automatiquement en cas d'erreur.

### Génération du barème
- Utilisez `subject_analysis.py` avec `corrige.md` pour générer un `bareme.csv` fidèle.
- Les marqueurs de question `(--Qx.y)` dans `sujet.md` doivent être placés **après** chaque question.

---

## Commandes CLI

### Générer le barème
```bash
python correct_ai/subject_analysis.py --project-dir mon_projet
```

### Corriger des copies
```bash
# Une copie spécifique
python correct_ai/batch_correction.py --project-dir mon_projet --start 1 --end 1 --force

# Toutes les copies
python correct_ai/batch_correction.py --project-dir mon_projet --start 1 --end 99

# Avec un provider spécifique
python correct_ai/batch_correction.py --project-dir mon_projet --provider UNISTRA_QWEN --start 1 --end 5
```

### Options disponibles
- `--start` / `--end` : Plage de copies (inclusif)
- `--provider` : Provider LLM (`UNISTRA_QWEN` par défaut)
- `--no-thinking` : Désactive le mode reasoning (pour les modèles Qwen)
- `--force` : Réécrase les corrections existantes

---

## Dépannage

### Erreurs JSON fréquentes
Si vous voyez `JSON Decode Error`, le module `json_repair` corrige automatiquement les erreurs d'échappement LaTeX (ex: `\frac`, `\sin`).

### Limites de tokens
Les limites sont gérées par le serveur. Pas de `max_tokens` imposé côté client.

### Thinking mode
- **UNISTRA** : Respecte le paramètre `enable_thinking` via `chat_template_kwargs`.
- **PALGANIA** : Nécessite aussi `chat_template_kwargs`, mais peut avoir un comportement différent selon la version.
