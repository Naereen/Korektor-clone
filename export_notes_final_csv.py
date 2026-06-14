import sqlite3
import csv

# Connexion à la DB
conn = sqlite3.connect('/Users/helluy/gitlab/korektor/exemple2/korektor.db')

# Paramètres
qcm_questions = ('Q1.1','Q1.2','Q2.1','Q2.2','Q3.1','Q3.2','Q4.1','Q4.2','Q5.1','Q5.2')
alpha = 1.723495
beta = 0.510963

# Ouvrir le CSV en écriture
with open('/Users/helluy/gitlab/korektor/example2/notes_finales.csv', 'w', newline='') as csvfile:
    writer = csv.writer(csvfile)
    
    # Écrire l'en-tête
    writer.writerow(['nom', 'prenom', 'num_copie', 'note'])
    
    # Récupérer toutes les copies
    cursor = conn.execute('''
        SELECT co.Copie_ID, co.Nom, co.Prenom
        FROM correspondance co
        ORDER BY co.Copie_ID
    ''')
    
    for row in cursor:
        copie_id, nom, prenom = row
        cursor2 = conn.execute('''
            SELECT b.question, cor.note_llm, b.points
            FROM corrections cor
            JOIN bareme b ON cor.bareme_id = b.id
            WHERE cor.copie_id = ?
        ''', (copie_id,))
        
        note_qcm25 = 0
        for q, note_llm, points in cursor2:
            if q in qcm_questions:
                note_qcm25 += (note_llm / points) * 0.25
            else:
                note_qcm25 += (note_llm / points)
        
        note_final = alpha * note_qcm25 + beta
        writer.writerow([nom, prenom, copie_id, round(note_final, 2)])

conn.close()
print("✅ Export CSV terminé : example2/notes_finales.csv")
print("\nFormat : nom,prenom,num_copie,note")
