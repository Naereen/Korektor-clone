# 📋 À Faire : État Actuel & Prochaines Étapes pour Korektor

---

## 🎯 **Contexte Global**
Korektor est une application web Flask conçue pour automatiser le workflow de correction de copies d'examen manuscrites.
**Objectif du Plan 01** : Implémenter une approche incrémentale avec base de données SQLite pour garantir l'uniformité des notes.

---

## ✅ **ÉTAT ACTUEL DU CODE**

### 🏗 Architecture Implémentée
```
correct_ai/
├── app.py                  # ✅ Routes API DB: /api/db/bareme, /api/db/copies, /api/db/corrections
│                           # ✅ Onglet "traitement" utilise batch_correction_v2 (incrémental)
├── database.py             # ✅ Module SQLite complet (tables: sujet, corrige, bareme, copies, corrections, stats)
├── batch_correction.py     # ⚠️  Ancien système (CSV) - plus utilisé pour les corrections
├── batch_correction_v2.py  # ✅ Correction incrémentale avec contexte historique (10 copies aléatoires)
│                           # ✅ Utilisé par l'onglet "traitement" via /api/batch/start
├── batch_extraction.py     # ✅ Extraction OCR (fichiers) compatible avec load_all_extractions
├── prompt_builder.py      # ✅ Prompts avec demande explicite de raisonnement_llm
├── llm_providers.py        # ✅ Module existant + ask_llm_with_progress() (streaming + logging)
├── templates/
│   ├── verify.html          # ✅ 5 champs clairs: Réponse Étudiant, Barème, Commentaire, Raisonnement LLM, Note
│   ├── verify_ocr.html      # ✅ Onglet dédié pour vérification OCR
│   └── layout.html         # ✅ Lien vers "Vérification OCR"
└── tests/                 # ✅ Tests unitaires créés
```

### 🔄 Workflow Actuel
1. **Import** → Upload PDF A3, découpage en A4
2. **Découpage** → Identification des copies par étudiant
3. **Extraction OCR** → Transcription page par page (fichiers .md)
4. **Correction Incrémentale** → Via onglet "traitement" → utilise batch_correction_v2 avec contexte des 10 copies précédentes, sauvegarde en DB SQLite avec `raisonnement_llm`
5. **Vérification** → 5 champs clairs, données depuis DB
6. **Vérification OCR** → Édition manuelle des extractions

### 📊 Données Actuelles
- **Base SQLite** : `korektor.db` avec tables sujet, corrige, bareme, copies, corrections, stats
- **Copies corrigées** : 10/31 (1-5, 17-21) avec raisonnement_llm
- **Routes API** : `/api/db/*` fonctionnelles
- **Interface** : 5 champs affichent correctement les données de la DB

### ✅ Fonctionnalités Implémentées
| Fonctionnalité | Statut |
|---------------|--------|
| Base SQLite | ✅ |
| Routes API DB | ✅ |
| Correction incrémentale | ✅ |
| Sélection aléatoire (10 copies) | ✅ |
| Raisonnement LLM | ✅ |
| Streaming + Logging | ✅ |
| Retry automatique (2x) | ✅ |
| Protection données exemple2/ | ✅ |
| Interface Vérification (5 champs) | ✅ |
| Interface Vérification OCR | ✅ |
| Tests unitaires | ✅ |

---

## 🚧 **À FAIRE (Priorité: Compatibilité DB SQLite)**

### 🔴 **HAUTE PRIORITÉ**

- [ ] **Migrer batch_extraction.py vers SQLite**
  - **Problème** : Sauvegarde uniquement dans fichiers (pageYY.md, extraction.md)
  - **À faire** : Utiliser `save_extraction(db, copy_id, pdf_path, extraction_text)`
  - **⚠️ CRITIQUE** : Respecter `extraction_verified=1` pour exemple2/ (NE PAS ÉCRASER)
  - **Code** :
    ```python
    from correct_ai.database import get_db, save_extraction
    db = get_db(project_dir)
    save_extraction(db, copy_id, pdf_path, extraction_text, verified=False)
    db.close()
    ```
PH: pas d'accord je préfère garder cet ancien pipeline pour faire le premier ocr compatible avec l'onglet vérification OCR.

- [x] **Brancher batch_correction_v2 dans l'onglet "traitement"**
  - **Décision** : Utiliser batch_correction_v2 (déjà incrémental avec raisonnement_llm) au lieu de modifier v1
  - **Fait** : Modified app.py BatchJob.start_batch_function pour utiliser batch_correction_v2.correct_copy_incremental
  - **Comportement** : 
    - Si force=False: saute les copies déjà corrigées (via has_corrections)
    - Si force=True: supprime les corrections existantes avant de relancer
    - Charge automatiquement sujet/corrige/bareme/extractions dans la base
    - Utilise la base SQLite (exemple2/korektor.db)
  - **Test** : Lancer correction via interface Flask /process → doit utiliser v2
  - **Remarque** : L'ancien batch_correction.py n'est plus utilisé pour les corrections

- [ ] **Chargement automatique des données au démarrage Flask**
  - **Problème** : Il faut charger manuellement sujet/corrige/bareme dans la DB
  - **À faire** : Charger automatiquement au premier accès ou via script d'init
  - **Script suggéré** : `python correct_ai/init_db.py exemple2`
PH: OK regarder aussi comment on génère le bareme à partir de sujet.md et corrige.md

- [ ] **Vérifier la protection des données exemple2/**
  - **Test** : Vérifier que `save_extraction()` retourne False si `extraction_verified=1`
  - **Commande** :
    ```bash
    python -c "
    from correct_ai.database import get_db, save_extraction
    db = get_db('exemple2')
    result = save_extraction(db, 1, 'test.pdf', 'nouveau texte', verified=False)
    print(f'Autorisé: {result}')  # Doit être False
    db.close()
    "
    ```

### 🟡 **MOYENNE PRIORITÉ**

- [ ] **Optimiser le prompt incrémental**
  - Limiter le nombre de copies précédentes (max 15)
  - Compresser les informations pour rester sous 200K tokens

- [ ] **Permettre l'édition du raisonnement_llm**
  - Actuellement en lecture seule dans verify.html
  - Option : champ éditable avec confirmation

- [ ] **Script de migration CSV → SQLite**
  - Exporter corrections depuis SQLite vers CSV
  - Importer anciens CSV dans SQLite

- [ ] **Backup automatique de la DB**
  - Sauvegarder `korektor.db` avant chaque batch
  - Script : `correct_ai/backup_db.py`

### 🟢 **BASSE PRIORITÉ**

- [ ] Ajouter des stats détaillées (temps moyen, tokens, distribution des notes)
- [ ] Interface de visualisation des stats (graphiques, export PDF/Excel)
- [ ] Caching des prompts communs (sujet, corrige, bareme)
- [ ] Parallélisation des requêtes API
- [ ] Validation automatique des notes (détection d'incohérences)
- [ ] Export complet des corrections
- [ ] Filtrage des copies dans l'interface

---

## 📝 **ÉTAT DES COPIES (exemple2)**

### ✅ Corrigées (10/31)
```
Copie 1-5, 17-21 : ✅ 26/26 corrections + raisonnement_llm
```

### ❌ À corriger (11/31)
```
Copie 16, 22-31 : ❌ 0/26 corrections
```

**Commande pour corriger les manquantes** :
```bash
# Via CLI:
python correct_ai/batch_correction_v2.py --project-dir exemple2 --start 16 --end 31

# Via interface Flask:
# 1. Lancer: python correct_ai/app.py
# 2. Aller sur http://127.0.0.1:5000/process
# 3. Sélectionner plage: 16 à 31
# 4. Cliquer "Lancer Correction"
```

---

## 🎯 **COMMENT TESTER**

### Interface Vérification
```bash
python correct_ai/app.py
# Ouvrir: http://127.0.0.1:5000/verify
# Sélectionner copie 18 → Tous les 5 champs doivent être remplis
```

### API DB
```bash
curl http://127.0.0.1:5000/api/db/corrections/18 | python -m json.tool | head -20
```

### Vérifier les corrections
```bash
sqlite3 exemple2/korektor.db "SELECT COUNT(DISTINCT copie_id) FROM corrections;"
sqlite3 exemple2/korektor.db "SELECT copie_id, COUNT(*) FROM corrections GROUP BY copie_id;"
```

---

## 📌 **NOTES TECHNIQUES**

### Clés API (⚠️ SANS ACCENTS)
- `reponses etudiant` (pas `réponses étudiant`)
- `commentaires eventuels du correcteur` (pas `commentaires éventuels...`)
- `reponse_attendue` (pas `réponse attendue`)
- `type_qcm` (pas `qcm (oui/non)`)

### Base SQLite
- **Fichier** : `exemple2/korektor.db`
- **Tables** : sujet, corrige, bareme, copies, corrections, stats
- **Index** : idx_corrections_copie, idx_corrections_bareme

### Correction Incrémentale
- **Sélection** : 10 copies aléatoires parmi celles déjà corrigées
- **Avantage** : Uniformité des notes, complexité O(1)
- **Streaming** : Logging toutes les 30 secondes

---

## 🚀 **COMMANDES UTILES**

### Initialiser DB
```bash
python -c "
from correct_ai.database import get_db, init_db, load_sujet, load_corrige, load_bareme
from pathlib import Path
project_dir = 'exemple2'
db = get_db(project_dir)
init_db(db)
load_sujet(db, Path(project_dir)/'sujet.md')
load_corrige(db, Path(project_dir)/'corrige.md')
load_bareme(db, Path(project_dir)/'bareme.csv')
db.close()
print('✅ DB initialisée')
"
```

### Marquer exemple2 comme vérifié
```bash
python -c "
from correct_ai.database import get_db, mark_extractions_verified
mark_extractions_verified(get_db('exemple2'), 'exemple2', verified=True)
print('✅ Extractions exemple2 protégées')
"
```

### Corriger copies manquantes
```bash
python correct_ai/batch_correction_v2.py --project-dir exemple2 --start 16 --end 31
```

---

## 💡 **DÉPANNAGE**

### Champs vides dans l'interface
→ Vérifier les noms de clés (sans accents) :
```bash
curl http://127.0.0.1:5000/api/db/corrections/18 | python -c "import sys,json;print(list(json.load(sys.stdin)[0].keys()))"
```

### PDF not found
→ Utiliser le format `Copie_XX` (ex: Copie_01, Copie_02) :
```javascript
const copyIdFormatted = `Copie_${String(copyId).padStart(2, '0')}`;
```

---

*Mise à jour: 13/06/2025 - Après implémentation complète*
