import sqlite3
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Connexion à la DB
conn = sqlite3.connect('/Users/helluy/gitlab/korektor/exemple2/korektor.db')

# Paramètres
qcm_questions = ('Q1.1','Q1.2','Q2.1','Q2.2','Q3.1','Q3.2','Q4.1','Q4.2','Q5.1','Q5.2')
MOYENNE_CIBLE = 12.0
ECART_TYPE_CIBLE = 3.0

# Calculer les notes QCM=0.25
notes_qcm25 = []
for copie_id in range(1, 32):
    cursor = conn.execute('''
        SELECT b.question, cor.note_llm, b.points
        FROM corrections cor
        JOIN bareme b ON cor.bareme_id = b.id
        WHERE cor.copie_id = ?
    ''', (copie_id,))
    
    note = 0
    for q, note_llm, points in cursor:
        if q in qcm_questions:
            note += (note_llm / points) * 0.25
        else:
            note += (note_llm / points)
    notes_qcm25.append(note)

notes_qcm25 = np.array(notes_qcm25)
mu = np.mean(notes_qcm25)
sigma = np.std(notes_qcm25)

# Calculer alpha et beta
alpha = ECART_TYPE_CIBLE / sigma
beta = MOYENNE_CIBLE - alpha * mu

# Appliquer la transformation
notes_finales = alpha * notes_qcm25 + beta

print(f"Paramètres calculés pour moyenne={MOYENNE_CIBLE}, écart-type={ECART_TYPE_CIBLE}:")
print(f"alpha = {alpha:.6f}")
print(f"beta = {beta:.6f}")
print(f"\nVérification:")
print(f"Moyenne: {np.mean(notes_finales):.4f}")
print(f"Écart-type: {np.std(notes_finales):.4f}")
print(f"Min: {np.min(notes_finales):.2f}")
print(f"Max: {np.max(notes_finales):.2f}")

# Créer l'histogramme avec bins de 1 point
plt.figure(figsize=(12, 6))

plt.hist(notes_finales, bins=range(0, int(max(notes_finales)) + 2, 1), 
         color='steelblue', edgecolor='black', alpha=0.7)

# Ajouter la moyenne
plt.axvline(x=MOYENNE_CIBLE, color='red', linestyle='-', linewidth=2, 
           label=f'Moyenne = {MOYENNE_CIBLE}')

plt.title(f'Histogramme - QCM=0.25, α={alpha:.3f}, β={beta:.3f} (moyenne={MOYENNE_CIBLE}, écart-type={ECART_TYPE_CIBLE})', 
          fontsize=14)
plt.xlabel('Note')
plt.ylabel('Nombre de copies')
plt.grid(True, axis='y', alpha=0.3)
plt.legend()

plt.tight_layout()

# Sauvegarder
plt.savefig(f'/Users/helluy/gitlab/korektor/example2/histogramme_moy{MOYENNE_CIBLE}_sigma{ECART_TYPE_CIBLE}.png', dpi=300)
print(f"\n✅ Histogramme sauvegardé dans example2/histogramme_moy{MOYENNE_CIBLE}_sigma{ECART_TYPE_CIBLE}.png")

# Distribution
binned = pd.cut(notes_finales, bins=range(0, int(max(notes_finales)) + 2, 1))
print("\n📊 Distribution par bins de 1 point :")
print(binned.value_counts().sort_index())

conn.close()
