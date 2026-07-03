choses à faire pour améliorer korektor:

UI: changer l'architecture des onglets, mettre dans cet ordre:
- Import (définition du dossier de travail, vérif de la liste des fichiers, import des fichiers)
- Découpage: attribution des copies aux étudiants, interface actuelle OK
- OCR: boite de dialogue pour lancer l'OCR sur un lot de copies
- Vérification OCR: relecture par l'enseignant des textes reconnus par l'OCR, interface actuelle OK
- Correction: boite de dialogue pour lancer la correction automatique sur un lot de copies, vérification de base (nbre de questions corrigées, progression de la correction pour chaque copie, etc.-)
- Vérification correction: vérification par l'enseignant des corrections automatiques, interface actuelle OK
- Bilan: page qui affiche l'état actuel de la base koerektor.db (nombre de copies, nombre de questions corrigées, nombre de questions non corrigées, notes, etc.)


Fonctionnalités:

- dans l'UI: onglet Import proposer une boite de dialogue pour choisir le dossier du projet (au lieu de la roue peu visible en haut à droite). Cette étape doit faire un check rapide des fichiers présents et de leur structure. Génère le barème et et l'affiche pour validation par l'enseignant.
- onglet OCR: il faut un affichage convivial de la progression. Cet affichage doit résister à un changement d'onglet (il faut donc un thread séparé pour l'OCR). Il faut aussi un bouton "Annuler" pour arrêter l'OCR en cours.
- ongle Vérification OCR: actuellement, le process est risqué: on peut perdre l'OCR inital ou des corrections non sauvées par l'enseignant. Il faudrait aussi vérifier que le markdwon produit par le LLM compile notemment les formules LaTeX. Mettre un bouton "sauvegarder". Mettre un bouton "Annuler" pour revenir à l'état initial de la copie avant l'OCR. Proposer de sauvegarder quand on change de copie ou d'onglet.
- onglet Correction: il faut un affichage convivial de la progression. Cet affichage doit résister à un changement d'onglet (il faut donc un thread séparé pour la correction). Pendnat la progression et à la fin signaler les copies problématiques (où la correction automatique n'a pas complètement abouti). Il faut aussi un bouton "Annuler" pour arrêter la correction en cours. Si des copies ne sont pas bien corrigées, proposer de les refaire.
- vérification correction: actuellement pas idéal, mais on verra plus tard.

implémenter ces modifications dans l'UI et les fonctionnalités pour améliorer l'expérience utilisateur et la robustesse de korektor.
S'arrêter fréquemment pour me demander de tester l'interface. Prévoir des tests unitaires pour chaque fonctionnalité implémentée de préférence sans intervention d'un utuilisateur.

