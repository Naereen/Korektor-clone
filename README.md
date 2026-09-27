# Korektor - Assistant IA de Correction de Copies Manuscrites

<table>
  <tr>
    <td width="60%" valign="middle">
      <p>Korektor est une application Web (Flask) conçue pour aider à la correction de copies d'examen manuscrites en utilisant des modèles de langage (LLM).</p>
      <p><strong>Fonctionnalités :</strong></p>
      <ol>
        <li><strong>Numérisation et Import</strong> : Import direct de scans A4 ou découpage intelligent de scans (A3 -> A4), avec nombre de pages par copie configurable, puis regroupement par étudiant.</li>
        <li><strong>Structuration du Sujet</strong> : Analyse du sujet et du corrigé pour générer un barème structuré (questions, réponses attendues, points).</li>
        <li><strong>Extraction (OCR)</strong> : Transcription du contenu manuscrit en Markdown avec structuration assistée (questions, réponses).</li>
        <li><strong>Correction IA</strong> : Notation assistée générant des notes et des commentaires. La correction considère à la fois le texte et l'image et l'historique des corrections des copies précédentes.</li>
        <li><strong>Vérification Professeur</strong> : Interface (relativement) ergonomique pour relire les copies, ajuster les notes et annoter.</li>
        <li><strong>Bilan graphique</strong> : Affichage de plusieurs visualisations de l'évaluation, avec téléchargement individuel ou export global en PNG.</li>
      </ol>
    </td>
    <td width="30%" valign="middle">
      <img src="korektor.jpeg" alt="Korektor" width="100%">
    </td>
  </tr>
</table>

## Installation

1.  **Prérequis** : Python 3.12+, [uv](https://docs.astral.sh/uv/)
2.  **Créer l'environnement virtuel et installer les dépendances** :
    ```bash
    uv venv
    source .venv/bin/activate
    uv pip install -r requirements.txt
    ```
3.  **Récupérer une clé d'API LLM** sur [https://conversation.ia.unistra.fr/](https://conversation.ia.unistra.fr/) et la définir dans vos variables d'environnement :
    ```bash
    export UNISTRA_API_KEY="votre_cle_ici"
    ```

## Utilisation

1.  **Lancer l'application** :
    ```bash
    uv run python correct_ai/app.py
    ```
    L'interface est accessible sur [http://127.0.0.1:5000](http://127.0.0.1:5000).

2.  **Configurer le projet** :
    *   L'application travaille par défaut dans le dossier `./exemple`.
    *   Le dossier de travail se change depuis l'onglet **Import** (champ "Dossier du projet").
    *   Les dossiers validés sont mémorisés dans ce navigateur. Double-cliquez dans le champ ou appuyez sur ↓ pour retrouver les chemins récemment utilisés ; la saisie manuelle et le sélecteur de dossier restent disponibles.
    *   Fichiers requis : `sujet.md`, `corrige.md`, `liste-etudiants.csv`.
    *   Le `bareme.csv` est **généré automatiquement** depuis l'onglet Import (via `subject_analysis.py`). L'onglet **Vérif Barème** permet de contrôler et modifier chaque question, y compris les réponses multilignes, avant la correction. Les changements enregistrés sont aussi reportés dans `korektor.db` si la base existe déjà.

3.  **Procédure** :
    *   **Import** : Définissez le dossier projet, vérifiez la structure des fichiers, générez le barème, importez le PDF scanné et indiquez le nombre de pages A4 constituant une copie. La valeur proposée est 2 pour un PDF déjà en A4 et 4 pour un livret A3.
    *   **Vérif Barème** : Recherchez, filtrez et modifiez les questions, leur type, leurs points et leurs réponses. Ajoutez ou réordonnez des questions, puis enregistrez avant de poursuivre vers Découpage. Une question déjà corrigée ne peut pas être supprimée.
    *   **Découpage** : Choisissez l'import direct A4 ou le découpage A3→A4, puis validez les débuts de copie et l'identification des étudiants. Le nombre de pages choisi à l'import détermine les aperçus et les PDF individuels générés.
    *   **OCR** : Lancez l'extraction OCR sur un lot de copies (bouton Annuler disponible).
    *   **Vérif OCR** : Relisez et corrigez le texte extrait page par page dans un éditeur CodeMirror Markdown avec numéros de ligne, puis contrôlez le rendu LaTeX dans l'aperçu.
    *   **Correction** : Lancez la correction assistée incrémentale sur un lot de copies.
    *   **Vérif Correction** : Passez en revue chaque copie : réponses étudiant, barème, commentaires, raisonnement LLM, notes.
    *   **Bilan** : Dashboard avec l'état global, les statistiques de classe, les notes et les copies problématiques. La page affiche la distribution des notes sur 20, la difficulté des questions, le profil moyen de la classe, la dispersion par question et les résultats détaillés d'un étudiant ou de la moyenne de la classe. Chaque graphique peut être téléchargé séparément en PNG ou tous les graphiques peuvent être exportés en une fois dans `graphiques_notes/`.

    Le parcours est accéléré entre les étapes : un import, un découpage ou un traitement batch terminé avec succès ouvre automatiquement l'étape suivante. Les pages de relecture **Vérif OCR** et **Vérif Correction** proposent un bouton explicite de fin qui sauvegarde les modifications avant de continuer. Le **Bilan** fournit enfin un accès direct aux **Rapports**. Une annulation ou une erreur de traitement ne déclenche aucune redirection.

## Structure des Données

### Base de Données SQLite (`korektor.db`)
Le système utilise désormais une base SQLite pour stocker toutes les corrections avec :
*   Table `sujet` : Contenu du sujet
*   Table `corrige` : Corrigé officiel
*   Table `bareme` : Barème avec questions, réponses attendues, points
*   Table `copies` : Métadonnées des copies
*   Table `corrections` : **Toutes les corrections avec `raisonnement_llm`** (note, commentaire, raisonnement détaillé, champs QCM). Les colonnes facultatives `crop_image_path` (chemin du PNG) et `bounding_boxes` (texte JSON) conservent les extraits de questions et leurs zones. Les anciennes bases sont migrées automatiquement à leur ouverture.

Les zones sont enregistrées sous la forme `[{"box_2d": [ymin, xmin, ymax, xmax], "label": "Erreur", "page": 2}]`, avec des coordonnées entières de 0 à 1000 relatives à la page. Le champ `page` (numérotation à partir de 1) est requis pour recadrer une copie multipage ; il peut être omis pour une page unique. Les boîtes absentes ou invalides deviennent `[]`. Le premier recadrage exploitable de chaque question est enregistré dans `cropped_questions/` par `correct_ai.image_processing` ; si le PDF est inaccessible ou la page inconnue, les boîtes restent enregistrées sans chemin PNG.

Certaines rubriques ne sont pas encore utilisées, mais la base est un bon point de départ pour vérifier ou affiner les corrections en discutant avec des agents IA.

### Fichiers par copie (ex: `Copie_01`)
Le système conserve aussi :
*   `Copie_01.pdf` : Le scan individuel.
*   `extraction.md` : Le contenu transcrit (vérifiable via l'onglet "Vérification OCR").
*   `page<xx>.md` : Chaque page du scan est sauvegardée séparément pour faciliter la vérification.
*   `.korektor_split.json` : Le nombre de pages A4 par copie retenu lors du dernier import du projet. Les anciens projets sans ce fichier utilisent 4 pages par copie.

## Configuration API

Les clés API pour les LLM (Mistral, OpenAI, etc.) doivent être définies dans vos variables d'environnement ou configurées via la classe `ProviderConfig` dans `correct_ai/llm_providers.py`. Par défaut , le provider utilisé est `UNISTRA_QWEN` (Qwen3) avec `thinking=True`. Pour utiliser un autre LLM, comme Mistral, définissez la variable d'environnement suivante :

```bash
export MISTRAL_API_KEY="votre_cle_ici"
```

---

## Configuration Recommandée

### Provider par défaut
- **UNISTRA_QWEN** (Qwen3) avec `thinking=True` : Le plus fiable pour la correction.

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
GET /api/bilan/stats
```

## Commandes CLI

### Générer le barème
```bash
python correct_ai/subject_analysis.py --project-dir mon_projet
```

### Corriger des copies (v2, incrémentale)
```bash
# Toutes les copies
uv run python correct_ai/batch_correction_v2.py --project-dir mon_projet --start 1 --end 31

# Mode test (sans appel LLM)
uv run python correct_ai/batch_correction_v2.py --project-dir mon_projet --start 1 --end 5 --test-mode
```

### Options disponibles
- `--start` / `--end` : Plage de copies (inclusif)
- `--provider` : Provider LLM (`UNISTRA_QWEN` par défaut)
- `--test-mode` : Mode test sans appel LLM réel

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

## Interface : Vérification OCR

L'onglet **Vérif OCR** permet de vérifier et corriger manuellement l'extraction :

- Visualisation page par page des PDF
- Édition du contenu Markdown extrait avec CodeMirror, coloration Markdown, numéros de ligne et retour automatique à la ligne
- Prévisualisation en temps réel avec rendu LaTeX (MathJax)
- **Bouton Sauvegarder** pour enregistrer la page courante
- **Bouton Annuler** pour revenir à la dernière version sauvegardée
- **Détection d'erreurs LaTeX** (dollars impairs, backslashs non échappés)
- Confirmation avant de quitter une page modifiée
- Reconstruction de `extraction.md` à partir de toutes les pages
