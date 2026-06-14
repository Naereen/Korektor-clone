import sqlite3
import csv
import os

os.chdir('/Users/helluy/gitlab/korektor')

conn = sqlite3.connect('/Users/helluy/gitlab/korektor/example2/korektor.db')
cursor = conn.cursor()

# Créer la table correspondance
cursor.execute('''
CREATE TABLE IF NOT EXISTS correspondance (
    copie_id INTEGER PRIMARY KEY,
    fichier TEXT,
    nb_pages INTEGER,
    page_range TEXT,
    etudiant_id TEXT,
    nom TEXT,
    prenom TEXT,
    FOREIGN KEY(copie_id) REFERENCES copies(id)
)
''')

# Lire le CSV et insérer les données
with open('/Users/helluy/gitlab/korektor/example2/correspondance.csv', 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        cursor.execute('''
        INSERT OR REPLACE INTO correspondance 
        (copie_id, fichier, nb_pages, page_range, etudiant_id, nom, prenom)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (int(row['Copie_ID']), row['Fichier'], int(row['Nb_Pages']), row['Page_Range'], 
              row['Etudiant_ID'], row['Nom'], row['Prenom']))

conn.commit()
print(f'Table "correspondance" créée et {cursor.rowcount} lignes insérées')

# Vérification
cursor.execute('SELECT COUNT(*) FROM correspondance')
count = cursor.fetchone()[0]
print(f'Vérification: {count} lignes dans la table correspondance')

# Afficher un exemple
cursor.execute('SELECT * FROM correspondance LIMIT 5')
rows = cursor.fetchall()
print('\nExemple de données:')
for row in rows:
    print(row)

conn.close()
