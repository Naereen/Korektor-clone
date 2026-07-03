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

1.  **Prérequis** : Python 3.12+, [uv](https://docs.astral.sh/uv/)
2.  **Installer les dépendances** :
    ```bash
    uv sync
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
    *   Fichiers requis : `sujet.md`, `corrige.md`, `liste-etudiants.csv`.
    *   Le `bareme.csv` est **généré automatiquement** depuis l'onglet Import (via `subject_analysis.py`). Il sert de format intermédiaire : l'enseignant peut le vérifier et l'ajuster (Excel, tableur) avant qu'il soit chargé dans la base `korektor.db` pour la correction.

3.  **Workflow** :
    *   **Import** : Définissez le dossier projet, vérifiez la structure des fichiers, générez le barème, importez le PDF scanné.
    *   **Découpage** : Validez le découpage A3→A4 et l'identification des étudiants.
    *   **OCR** : Lancez l'extraction OCR sur un lot de copies (bouton Annuler disponible).
    *   **Vérif OCR** : Relisez et corrigez le texte extrait page par page, avec aperçu LaTeX.
    *   **Correction** : Lancez la correction automatique incrémentale sur un lot de copies.
    *   **Vérif Correction** : Passez en revue chaque copie : réponses étudiant, barème, commentaires, raisonnement LLM, notes.
    *   **Bilan** : Dashboard avec l'état global : nombre de copies, notes, copies problématiques.

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
- Édition du contenu Markdown extrait
- Prévisualisation en temps réel avec rendu LaTeX (MathJax)
- **Bouton Sauvegarder** pour enregistrer la page courante
- **Bouton Annuler** pour revenir à la dernière version sauvegardée
- **Détection d'erreurs LaTeX** (dollars impairs, backslashs non échappés)
- Confirmation avant de quitter une page modifiée
- Reconstruction de `extraction.md` à partir de toutes les pages

## Dépannage

### Erreurs JSON fréquentes
Si vous voyez `JSON Decode Error`, le module `json_repair` corrige automatiquement les erreurs d'échappement LaTeX (ex: `\frac`, `\sin`).

### Limites de tokens
Les limites sont gérées par le serveur. Pas de `max_tokens` imposé côté client.

### Thinking mode
- **UNISTRA** : Respecte le paramètre `enable_thinking` via `chat_template_kwargs`.
- **PALGANIA** : Nécessite aussi `chat_template_kwargs`, mais peut avoir un comportement différent selon la version.
