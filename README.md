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
    *   **Extraction OCR** : Transcrivez le contenu manuscrit en Markdown via l'onglet "Vérification OCR".
    *   **Traitement** (Batch) : Lancez la correction IA pour tout ou partie des copies.
    *   **Vérification** : Passez en revue chaque copie via l'onglet "Vérification". L'interface affiche :
        *   Le PDF à gauche
        *   **5 champs clairs** à droite :
            - Réponse Étudiant (les réponses transcrites)
            - Barème / Réponse Attendue (le corrigé)
            - Commentaire (retour du LLM)
            - **Raisonnement LLM** (explication détaillée du processus de correction)
            - Note + champs QCM (si applicable)
        *   Modifiez les notes ou commentaires si nécessaire.
        *   Cliquez sur "Enregistrer Tout". Vos modifications sont sauvées dans la **base SQLite** (`korektor.db`).

## Structure des Données

### Base de Données SQLite (`korektor.db`)
Le système utilise désormais une base SQLite pour stocker toutes les corrections avec :
*   Table `sujet` : Contenu du sujet
*   Table `corrige` : Corrigé officiel
*   Table `bareme` : Barème avec questions, réponses attendues, points
*   Table `copies` : Métadonnées des copies
*   Table `corrections` : **Toutes les corrections avec `raisonnement_llm`** (note, commentaire, raisonnement détaillé, champs QCM)

### Fichiers par copie (ex: `Copie_01`)
Pour compatibilité, le système conserve aussi :
*   `Copie_01.pdf` : Le scan individuel.
*   `extraction.md` : Le contenu transcrit (vérifiable via l'onglet "Vérification OCR").
*   `correction.csv` : Ancienne correction IA (compatibilité).
*   `correction_prof.csv` : Ancienne correction validée (compatibilité).

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

## API REST

### Endpoints de Base de Données (SQLite)
Des nouvelles routes permettent d'accéder directement aux données depuis la base :

```bash
# Récupérer le barème depuis la DB
GET /api/db/bareme

# Lister toutes les copies depuis la DB  
GET /api/db/copies

# Récupérer toutes les corrections d'une copie (avec raisonnement_llm)
GET /api/db/corrections/<copie_id>

# Statistiques de correction
GET /api/db/stats
```

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

### Correction Incrémentale (v2)
Le module `batch_correction_v2.py` implémente une approche avancée :

```bash
# Correction incrémentale avec raisonnement détaillé
python correct_ai/batch_correction_v2.py --project-dir exemple2 --start 1 --end 20

# Avec sélection aléatoire de copies précédentes pour contexte
python correct_ai/batch_correction_v2.py --random-context 10
```

**Fonctionnalités** :
- **Raisonnement détaillé** : Chaque correction inclut un champ `raisonnement_llm` expliquant le processus
- **Contexte historique** : Le LLM utilise les corrections des copies précédentes pour uniformiser les notes
- **Sélection aléatoire** : Jusqu'à 10 copies déjà corrigées sont sélectionnées aléatoirement pour le contexte
- **Retry automatique** : En cas d'échec API, retry 2 fois avant de passer à la copie suivante
- **Logging** : Progress logging avec timestamps toutes les 30 secondes

---

## Nouvelle Interface : Vérification OCR

Un nouvel onglet dédié permet de vérifier et corriger manuellement l'extraction OCR :

**Fonctionnalités** :
- Visualisation page par page des PDF
- Édition du contenu Markdown extrait
- Prévisualisation en temps réel
- Aperçu LaTeX via MathJax

**Accès** : `http://127.0.0.1:5000/verify_ocr`

## Dépannage

### Erreurs JSON fréquentes
Si vous voyez `JSON Decode Error`, le module `json_repair` corrige automatiquement les erreurs d'échappement LaTeX (ex: `\frac`, `\sin`).

### Limites de tokens
Les limites sont gérées par le serveur. Pas de `max_tokens` imposé côté client.

### Thinking mode
- **UNISTRA** : Respecte le paramètre `enable_thinking` via `chat_template_kwargs`.
- **PALGANIA** : Nécessite aussi `chat_template_kwargs`, mais peut avoir un comportement différent selon la version.
