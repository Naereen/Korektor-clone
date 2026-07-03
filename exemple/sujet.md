# Examen d'Informatique - L3

Consignes:

- Documents, téléphones, communications et regards en biais interdits

- Les copies seront corrigées par des êtres humains.

- La correction pourra être vérifiée par une IA.

- Afin d'améliorer la reconnaissance d'écriture par l'IA, il est recommandé de recopier la question avant de répondre et d'écrire **clairement et lisiblement**

- Justifier votre réponse. Puis, dans le cas de question à choix multiple **encadrer** votre choix final sur la copie (et non sur le sujet).  Par exemple, écrire, après le raisonnement: (...) "les réponses correctes sont donc **$\boxed{A, B}$**"

## Échauffement:  exercice corrigé, **recopier la question et la réponse intégralement**

**Q0.** Soit le code C++ suivant. Sélectionner les affirmations vraies concernant l'état final (après la dernière accolade)
```cpp
int j = 0;
for(int i=0; j<=3; i++){
    j += i;
}
```
- [ ] A. j=6, i est inaccessible
- [ ] B. j=6, i=3 
- [ ] C. j=4, i=3

Réponse: 
À la sortie de la boucle, où i a été déclaré, i devient inaccessible.
Les valeurs successives du couple (j,i) à la fin de chaque itération sont:
(0,0), (1,1), (2,3), (3,6). Comme j=6 >= 3, on sort de la boucle.
La bonne réponse est donc **$\boxed{A}$** (--Q0)

## Thème 1 : Bases du C (Types, Syntaxe, Pointeurs, Boucles)

**Q1.** Soit le code C++ suivant. Quelles sont les affirmations vraies concernant l'état de la mémoire et l'affichage final ?

```cpp
int a = 10;
int *p = &a;
int b = *p;
*p = 20;
b = 30;
std::cout << "a=" << a << ", b=" << b << std::endl;
```

- [ ] A. a vaut 10.
- [ ] B. a vaut 20.
- [ ] C. b vaut 20.
- [ ] D. b vaut 30.
- [ ] E. p contient l'adresse mémoire de b.
- [ ] F. p contient l'adresse mémoire de a.
- [ ] G. Modifier *p modifie b.

(--Q1)

**Q2.** Quelle est la sortie du programme suivant ?

```cpp
int tab[] = {10, 20, 30, 40, 50};
int *p = tab;
p += 2;
std::cout << *p << " et " << p[2];
```

- [ ] A. 10 et 20
- [ ] B. 30 et 20
- [ ] C. 30 et 50
- [ ] D. 20 et 30
- [ ] E. 40 et 50
- [ ] F. Le programme produit une erreur de segmentation (segfault).

(--Q2)

**Q3.** Boucles et structures de contrôle. Quelles affirmations sont correctes ?

- [ ] A. Une boucle do...while s'exécute toujours au moins une fois.
- [ ] B. Une boucle while s'exécute toujours au moins une fois.
- [ ] C. L'instruction break permet de sortir immédiatement d'un if.
- [ ] D. L'instruction continue permet de passer directement à l'itération suivante d'une boucle.
- [ ] E. Dans une boucle for(init; condition; increment), l'incrément est exécuté avant le corps de la boucle.

(--Q3)

**Q4.** Quelle est la valeur finale de j après l'exécution de ce code ?

```cpp
int j = 0;
for (int i = 0; i < 5; i++) {
    if (i == 2) continue;
    j++;
    if (j > 2) break;
}
```

- [ ] A. 2
- [ ] B. 3
- [ ] C. 4
- [ ] D. 5
- [ ] E. La boucle est infinie.

(--Q4)

**Q5.** À propos des références et des pointeurs en C++ :

- [ ] A. Une référence peut être réaffectée pour référencer une autre variable après son initialisation.
- [ ] B. Un pointeur peut valoir nullptr.
- [ ] C. Une référence ne peut pas être NULL (ou non initialisée) à sa déclaration.
- [ ] D. void* est un pointeur générique qui peut être déréférencé directement.
- [ ] E. Si int i = 26; et int &j = i;, alors modifier j modifie i.

(--Q5)

---

## Thème 2 : Complexité

**Q6.** La notation O(g(n)) pour une fonction f(n) signifie :

- [ ] A. Que f(n) est toujours supérieure à g(n) à partir d'un certain rang.
- [ ] B. Qu'il existe une constante c>0 telle que f(n)$\le$c$\cdot$g(n) pour n assez grand (borne supérieure).
- [ ] C. Que f(n) et g(n) sont équivalentes.
- [ ] D. Que l'algorithme prendra exactement g(n) secondes.
- [ ] E. C'est le temps d'exécution dans le pire des cas.

(--Q6)

**Q7.** Quelle est la complexité temporelle asymptotique de l'algorithme suivant en fonction de n ?

```cpp
// n est la taille du tableau
for (int i = 0; i < n; i++) {
    for (int j = 0; j < n; j++) {
        // Opération élémentaire en O(1)
    }
}
for (int k = 0; k < n; k++) {
    // Opération élémentaire en O(1)
}
```

- [ ] A. O(n)
- [ ] B. O(nlogn)
- [ ] C. O(n^2)
- [ ] D. O(n^3)
- [ ] E. O(n^2 + n)
- [ ] F. $\Theta$(n^2)

(--Q7)

**Q8.** Soit un algorithme récursif dont la complexité T(n) satisfait T(n)=2T(n/2)+O(n). Selon le Master Theorem (ou analyse classique), quelle est sa complexité ? (C'est le cas du Tri Fusion)

- [ ] A. O(n)
- [ ] B. O(logn)
- [ ] C. O(nlogn)
- [ ] D. O(n^2)
- [ ] E. O(2^n)

(--Q8)

**Q9.** Classer les complexités suivantes de la plus rapide (la moins coûteuse) à la plus lente (la plus coûteuse) pour n grand :

- [ ] A. O(1) < O(logn) < O(n) < O(nlogn) < O(n^2) < O(2^n)
- [ ] B. O(n) < O(logn) < O(nlogn) < O(n^2)
- [ ] C. O(1) < O(n) < O(logn) < O(n^2)
- [ ] D. O(logn) < O(1) < O(n^2) < O(2^n)

(--Q9)

**Q10.** Complexité des algorithmes de tri classiques :

Le tri rapide (QuickSort) est un algorithme de tri de type "diviser pour régner". Il choisit un élément pivot, partitionne le tableau en deux sous-tableaux (les éléments inférieurs au pivot et ceux supérieurs), puis trie récursivement les sous-tableaux.

- [ ] A. Le tri par insertion a une complexité de O(n) dans le meilleur des cas (tableau déjà trié).
- [ ] B. Le tri rapide (QuickSort) a une complexité pire cas de O(nlogn).
- [ ] C. Le tri fusion a une complexité pire cas de O(n^2).
- [ ] D. Le tri rapide a une complexité pire cas de O(n^2).
- [ ] E. Le tri fusion a une complexité moyenne de O(nlogn).

(--Q10)
---

## Thème 3 : Logique de Hoare

**Q11.** Quelle est la règle correcte pour l'affectation {P}x:=E{Q} en logique de Hoare ?

- [ ] A. $P \Rightarrow Q[x \leftarrow E]$
- [ ] B. $\{Q[x \leftarrow E]\} x:=E \{Q\}$ (La précondition est obtenue en remplaçant x par E dans la postcondition).
- [ ] C. $\{P\} x:=E \{P \land (x=E)\}$
- [ ] D. $\{P[x \leftarrow E]\} x:=E \{Q\}$

(--Q11)

**Q12.** Soit le triplet {P}x:=x+1{x>10}. Quelle est la précondition P la plus faible (la moins restrictive) ?

- [ ] A. x > 10
- [ ] B. x > 9
- [ ] C. $x \ge 9$
- [ ] D. $x \ge 10$
- [ ] E. x + 1 > 10

(--Q12)

**Q13.** Pour prouver la correction totale d'une boucle while, il faut :

- [ ] A. Trouver un invariant de boucle qui est vrai avant et après chaque itération.
- [ ] B. Prouver que la boucle s'arrête (terminaison).
- [ ] C. Prouver que la postcondition implique la précondition.
- [ ] D. Trouver un variant de boucle (entier positif qui décroît strictement).
- [ ] E. Prouver que l'invariant est faux à la sortie de la boucle.

(--Q13)

**Q14.** On considère le code suivant :

```cpp
// Précondition : { x >= 0 }
y = 1;
z = 0;
while (z != x) {
    z = z + 1;
    y = y * 2;
}
// Postcondition : { y = 2^x }
```

Quel est l'invariant de boucle correct parmi les suivants ?

- [ ] A. y = 2^x
- [ ] B. $y = 2^z \land z \le x$
- [ ] C. y = z^2
- [ ] D. z = x
- [ ] E. y = 2x

(--Q14)

**Q15.** En reprenant le code de la question précédente, quel est un variant de boucle valide pour prouver la terminaison ?

- [ ] A. z
- [ ] B. x
- [ ] C. x - z
- [ ] D. z - x
- [ ] E. y

(--Q15)

**Q16.** Soit le programme conditionnel suivant : `if (x >= y) then z := x else z := y`. On veut prouver que `{z = max(x,y)}` à la fin. Quelles sont les obligations de preuve générées par la règle de la conditionnelle ?

- [ ] A. $\{P \land (x \ge y)\} z:=x \{z=\max(x,y)\}$ et $\{P \land \neg(x \ge y)\} z:=y \{z=\max(x,y)\}$.
- [ ] B. $\{P\} z:=x \{z=\max(x,y)\}$
- [ ] C. $\{x \ge y\} z:=x \{z=x\}$
- [ ] D. Il faut prouver que z est toujours positif.

(--Q16)

---

## Thème 4 : C++ de base (Classes, Gestion mémoire)

**Q17.** Soit une classe Vecteur contenant un attribut `int* data` alloué dynamiquement dans le constructeur. Que se passe-t-il si on ne définit pas de destructeur explicitement ?

- [ ] A. Le compilateur génère une erreur.
- [ ] B. La mémoire pointée par data est libérée automatiquement à la fin de l'objet.
- [ ] C. Il y a une fuite de mémoire (memory leak) car le tableau n'est pas désalloué.
- [ ] D. Le pointeur data est mis à nullptr mais la mémoire reste occupée.

(--Q17)

**Q18.** Constructeur par copie en C++ :

- [ ] A. Il est appelé lorsqu'on passe un objet par valeur à une fonction.
- [ ] B. Il a pour signature `ClassName(ClassName & other)`.
- [ ] C. Il a pour signature `ClassName(const ClassName & other)`.
- [ ] D. Si on ne le définit pas, le compilateur en génère un qui fait une copie membre à membre (shallow copy).
- [ ] E. Il est obligatoire de le définir si la classe gère des pointeurs alloués dynamiquement (pour faire une copie profonde).

(--Q18)

**Q19.** Quelle est la différence entre `delete p` et `delete[] p` ?

- [ ] A. Aucune, les deux libèrent la mémoire.
- [ ] B. `delete p` libère un objet unique, `delete[] p` libère un tableau d'objets alloué avec `new []`.
- [ ] C. `delete[]` ne doit être utilisé que pour les tableaux de caractères (`char*`).
- [ ] D. Utiliser `delete` sur un tableau alloué avec `new []` a un comportement indéfini (souvent crash ou corruption).

(--Q19)

**Q20.** Dans la définition d'une méthode de classe `void afficher() const;` :

- [ ] A. Le mot-clé `const` signifie que la méthode ne retourne rien.
- [ ] B. Le mot-clé `const` signifie que la méthode ne peut pas modifier les attributs de l'objet appelant.
- [ ] C. Cette méthode peut être appelée sur un objet constant (`const MyClass obj`).
- [ ] D. Cette méthode ne peut pas appeler d'autres méthodes non-constantes de la même classe.

(--Q20)

**Q21.** Surcharge d'opérateurs (ex: `operator+`) :

- [ ] A. On peut surcharger l'opérateur `.` (point).
- [ ] B. Si on définit l'opérateur comme fonction externe, il faut souvent le déclarer `friend` pour accéder aux membres privés.
- [ ] C. L'opérateur d'affectation `operator=` doit obligatoirement être une méthode membre de la classe (pas une fonction externe).
- [ ] D. `a + b` est strictement équivalent à `a.operator+(b)` si l'opérateur est défini en interne.
- [ ] E. On peut changer la priorité des opérateurs (faire que `+` soit prioritaire sur `*`).

(--Q21)

**Q22a.** Écriture d'un destructeur pour la classe `BinTree` :

La classe `BinTree` alloue dynamiquement des nœuds dans ses méthodes `insert` et `insertleft`. Pour éviter les fuites de mémoire, il est nécessaire de définir un destructeur qui libère récursivement tous les nœuds de l'arbre.

Proposer une implémentation du destructeur pour la classe `BinTree` qui libère correctement la mémoire allouée.

(--Q22a)

---

## Thème 5 : Structures de Données, arbre binaire de recherche, récursivité

Un arbre binaire de recherche (ABR) est une structure de données qui permet d'insérer et de trouver rapidement un objet. Il est constitué de feuilles contenant des valeurs. Chaque feuille est reliée à un sous-arbre de gauche et un sous-arbre de droite. Les valeurs du sous-arbre de gauche sont strictement inférieures à la valeur de la feuille. Les valeurs du sous-arbre de droite sont supérieures ou égales à la valeur de la feuille.
Par convention, lorsqu'un sous-arbre est vide, on le représente par un pointeur nul (nullptr). Un ABR doit contenir au moins une feuille.
Exemples d'arbre:

**Arbre 1 avec une feuille :**
```
        5
     /     \
  nullptr nullptr
```
Nota: on peut omettre les nullptr pour alléger le dessin et représenter une feuille sans sous-arbre par :
```
        5
```

**Arbre 2 remplissant la condition d'ABR :**
```
           10
         /    \
        3    nullptr
       /  \
      1     5
           / \
          4   nullptr
```

Voici un début d'implémentation en C++ d'un ABR stockant des entiers :
```cpp
class BinTree {
public:
    BinTree(int n) : value(n), left(nullptr), right(nullptr) {}    
    void insert(int val);
    void affiche();
    bool contient(int val);

private:
    int value;
    BinTree* left;
    BinTree* right;
};
```
**Q22b.** Voici une proposition d'implémentation de la méthode `insert` qui insère une valeur dans l'arbre, mais toujours dans le sous-arbre gauche.

```cpp
void BinTree::insert(int val) {
    if (left == nullptr) {
        left = new BinTree(val);
    } else {
        left -> insert(val);
    }
}
```
Dessiner l'arbre 2 après insertion de la valeur 8 en utilisant cette méthode.

(--Q22b)

**Q23.** Proposer une implémentation correcte de la méthode `insert` qui insère une valeur dans l'arbre en respectant les propriétés d'un ABR.

Dessiner l'arbre 2 après insertion de la valeur 5 en utilisant votre méthode.

(--Q23)

**Q24.** Proposer une implémentation de la méthode `contient` qui retourne true si la valeur est présente dans l'arbre, false sinon. On utilisera une méthode de recherche efficace en exploitant les propriétés de l'ABR.

(--Q24)

**Q25.** Proposer une implémentation de la méthode `affiche` qui affiche les valeurs de l'arbre dans l'ordre croissant (du plus petit au plus grand). On utilisera une méthode efficace en exploitant les propriétés de l'ABR.

(--Q25)



