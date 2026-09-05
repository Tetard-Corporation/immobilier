# Les critères : registre, poids, et ce que la mesure vaut vraiment

Ce document répond à trois questions : **qu'est-ce qu'un critère** (et pourquoi il a
maintenant une identité), **comment chacun le pondère pour lui**, et — le plus utile —
**ce que les critères mesurent réellement sur le catalogue actuel**, chiffres à l'appui.

## 1. Un critère a une identité, pas seulement un libellé

Un critère de set s'écrit `{kind, label, weight, params}`. Le libellé est ce qu'on lit,
et il est bon qu'il encode les paramètres : « Prix entre 180 000 € et 250 000 € » dit
plus que « Budget ». Mais il ne peut pas servir de **clé** :

- il change dès qu'un paramètre change — le budget est passé de 600 k€ à 450 k€, puis
  300 k€, puis 250 k€ en trois tours de table ;
- il diffère d'un set à l'autre pour la même mesure : `nuisance_sonore` s'appelle « Loin
  d'une route passante / autoroute / rail » (têtard), « Au calme » (Pauline) et « Calme
  (loin autoroute/rail) » (Bretagne) ;
- le `kind` seul ne suffit pas non plus : le set breton a **cinq** préférences `feature`
  (bord de mer, bord d'eau, vue, en hauteur, isolé).

`backend/app/services/criteres.py` donne donc à chaque critère un **id stable**
(`budget`, `feature:bord_de_mer`…), une **famille** et un **nom canonique**. L'id part
dans `data.json` avec chaque préférence ; c'est sous lui que sont rangés les poids
personnels, et il survit à une reformulation.

Huit familles, dans l'ordre d'affichage : Prix & budget · Le bien · Cadre & nature ·
Village & services · Accès · Calme & nuisances · Risques & santé · Rendement locatif.

## 2. Chacun ses poids

Le front (`poids.js`) rejoue le calcul du backend **dans le navigateur**, sur les
sous-scores déjà mesurés et exportés bien par bien. Régler ses poids ne relance aucune
collecte et ne change aucune mesure : ça change la moyenne, donc le classement.

- **Échelle à 5 niveaux** (1 accessoire → 5 essentiel), plus **∅ ignorer** qui sort le
  critère du calcul. Un critère jamais réglé garde le poids du set.
- **Seuils personnels** : le poids dit *combien* un critère compte, pas *ce qu'on veut*.
  « 2 chambres minimum » et « 4 chambres minimum » sont deux exigences différentes, et
  deux personnes qui mettent 5 aux chambres ne cherchent pas la même maison. Sur les
  critères dont l'entrée est exportée bien par bien, chacun règle donc aussi le seuil —
  voir §2 bis.
- **Lentille** (barre du haut) : classer avec les poids du set, les miens, la moyenne du
  groupe, ou ceux de quelqu'un d'autre.
- **Panneau ⚖️** : le réglage, puis ce que le groupe en dit — accords, désaccords (avec
  les deux extrêmes nommés), proximité deux à deux.
- **Stockage** : la table `votes` existante, sous un bien fictif `__poids__:<set>`
  (`criterion` = l'id du critère, `stars` = le poids, `null` = ignorer). Aucune migration
  SQL. **Un script qui analyse les votes doit exclure `bien_id like '__poids__%'`.**

### 2 bis. Les seuils personnels, et ce qu'ils coûtent

Changer un seuil oblige à **recalculer le sous-score dans le navigateur** — donc à
réécrire dans `mesures.js` une formule qui vit dans `preferences.py`. Deux copies d'une
même règle finissent par diverger : trois garde-fous.

1. **Seuls les critères dont l'entrée est exportée bien par bien** sont portés : budget,
   chambres, format du logement, surface habitable, terrain, jardin, altitude, DPE. Pas
   l'exposition, pas la distance à la gare, pas le rapport qualité/prix : ces mesures-là
   ne se rejouent pas côté client, leurs paramètres restent ceux du set.
2. **La formule portée ne sert que si le seuil a été changé.** Tant qu'on garde celui du
   set, c'est le sous-score du backend qui fait foi — toujours.
3. **`tests/test_mesures.mjs` rejoue les formules portées sur tout le catalogue** avec
   les paramètres du set et vérifie qu'elles redonnent le sous-score exporté :
   21 949 sous-scores, **0 écart**. Une divergence se verrait là, pas dans un classement.

Un seuil personnel agit par la **note**, pas par une falaise : dire « mon budget est
120 k€ » fait descendre continûment les biens trop chers, chacun à sa place, au lieu de
les coller tous sur une même valeur (mesuré : 30 biens chers, 26 valeurs distinctes).

Le panneau ⚖️ affiche enfin **ce que chacun demande** — « chambres min : Léo 5, Max 2 » —
un désaccord que le poids seul ne peut pas exprimer.

### 2 ter. Ce que le seuil de publication décide, et que le poids ne rattrape pas

La pondération personnelle rejoue le classement **dans le navigateur, sur les biens
publiés**. Ce qui n'est pas exporté est donc hors d'atteinte : aucun poids, aucun seuil
personnel ne peut faire remonter un bien absent de `data.json`. Le seuil de publication
n'est pas un réglage d'affichage, c'est ce qui borne l'espace des choix de tout le monde.

Mesuré le 5 septembre 2026 sur les 3 831 biens du set dans sa zone :

| Seuil | Biens publiés | dont 2 chambres | dont ≤ 80 m² |
|---|---|---|---|
| 75,5 (l'ancien) | 20 | 2 | 0 |
| 72 | 52 | 8 | 3 |
| **65** | **665** | **111** | **71** |
| 25 | 3 823 | 624 | 803 |

Deux verrous se cumulaient et un seul était visible. Le **palier de capacité** exigeait
les 3 chambres pleines et plafonnait à 75 les 1 027 maisons de 2 chambres du catalogue,
soit 16 % ; le **seuil de publication** était à 75,5. Aucune ne pouvait donc apparaître,
et le réglage personnel ne pouvait rien y faire. Le palier descend à ce que valent deux
chambres (0,66), et le seuil à 65.

Attention au chiffre 70 : **157 biens notent exactement 70,0** parce qu'un palier les y
plafonne (budget, travaux ou jardin). Un seuil à 70 ferait entrer d'un coup tout ce qui
échoue à une exigence dure du groupe. Les seuils qui veulent dire quelque chose sont donc
au-dessus de 70, ou franchement en dessous.

Enfin, élargir le panier n'élargit pas les photos dans les mêmes proportions :
`EXPORT_PHOTOS_MIN` télécharge les images du seul haut du panier (plus les favoris et les
témoins de massif). Les autres biens s'affichent sans photo, avec la mention « N non
téléchargées » — le dossier `data/photos/` pèse déjà 1 Go et ne se committe pas en entier.

```bash
node tests/test_poids.mjs      # le recalcul contre les scores publiés
node tests/test_mesures.mjs    # les formules portées contre les sous-scores publiés
```

Le recalcul reproduit `services/preferences.evaluate` à l'identique : moyenne pondérée
des critères **mesurés**, contraste entre les mêmes ancres (0,20 / 0,90), paliers
d'exigences, pénalité des biens déclassés. Vérifié sur les 3 792 scores de l'instantané :
écart maximum **0,10** — l'arrondi du sous-score à l'export.

## 3. Ce que les critères mesurent vraiment (set têtard, 2 873 biens)

Mesuré sur l'instantané du 1er septembre 2026. À relire quand les poids du set changent.

### Certains critères ne départagent rien

Le pouvoir de discrimination d'un critère, c'est son poids **multiplié par l'écart-type**
de ses sous-scores. Un critère que tout le monde réussit ne classe personne.

| Critère | Poids | σ | Moyenne | Effet réel |
|---|---|---|---|---|
| Randonnées au départ | 3 | **0,02** | 1,00 | ≈ 0 — dit oui à tout le monde |
| ≥ 90 m² habitables | 1 | 0,14 | 0,93 | 0,14 |
| Fibre | 2 | 0,12 | 0,91 | 0,25 |
| Format maison de retrait | 4 | 0,17 | 0,95 | 0,68 |
| Montagne / relief | 5 | 0,30 | 0,54 | 1,50 |
| Coin de nature | 4 | 0,37 | 0,43 | 1,47 |

« Randonnées au départ » pèse 3 et vaut 1,00 pour 94 % des biens : il ne classe rien, il
remonte seulement tous les scores. Quatre critères de poids 4 (format, jardin, chambres,
travaux) sont au-dessus de 0,86 de moyenne : ce sont des **filtres déguisés en poids** —
ils décrivent ce que le groupe refuse, pas ce qui distingue deux bons candidats.

### Certains critères ne sont mesurés que sur une partie du catalogue

| Critère | Mesuré sur |
|---|---|
| Attractivité saisonnière (Airbnb) | 47 % |
| Exposition / soleil d'hiver | 48 % |
| Peu de travaux | 59 % |
| Grand terrain | 72 % |
| DPE | 74 % |
| Rapport qualité/prix | 76 % |
| Jardin | 77 % |
| Qualité de l'eau | 82 % |

`evaluate` renormalisait sur les seuls critères mesurés : **un bien dont l'exposition
n'avait jamais été calculée n'était pas pénalisé, il était jugé sans elle** — et il
montait. Deux paliers d'exigences fermaient cette porte en tête de classement. Depuis le
5 septembre 2026, c'est l'**a priori** qui la ferme partout : un critère non mesuré compte
à la moyenne du catalogue (exportée avec le critère), ni mieux ni moins bien.

La pondération personnelle rend ce point plus sensible : mettre 5 à l'exposition, c'est
classer la moitié du catalogue sans elle. Le panneau ⚖️ affiche donc la couverture de
chaque critère sous 80 %.

### Certains critères se paient deux fois

| Paire | r | Poids cumulé |
|---|---|---|
| Attractivité saisonnière ~ Station de ski | **0,80** | 4 + 2 |
| Jardin ~ Grand terrain | 0,72 | 4 + 3 |
| Chambres ~ Surface habitable | 0,61 | 4 + 1 |
| Cachet ~ Calme | 0,58 | 4 + 3 |

L'attractivité saisonnière est calculée à 80 % sur la présence d'une remontée mécanique,
que « Station de ski » mesure déjà : le ski pèse 6 dans un set où le poids maximum est 5.

## 4. Les trois critères ajoutés

Trois mesures existaient et n'alimentaient que le score d'investissement — que personne
ne regarde pour choisir, puisque le classement du site est le **match du set** :

| Critère | Poids têtard / Pauline / littoral | Mesuré sur (têtard) |
|---|---|---|
| `dpe` — performance énergétique | 3 / 4 / 2 | 74 % |
| `risques_naturels` — aléas Géorisques pondérés par gravité | 3 / 2 / 4 | 100 % |
| `qualite_eau` — Hub'Eau (pesticides, nitrates, PFAS) | 2 / 2 / 2 | 82 % |

- Le **DPE** dit ce que l'état du bâti ne dit pas : `light_works` lit « habitable / à
  rafraîchir / à rénover » dans l'annonce, le DPE lit ce que le bien coûtera à chauffer.
  Une maison habitable classée G est habitable **et** une passoire — interdite à la
  location depuis 2025. Il est mieux renseigné (74 %) que l'état du bâti (59 %).
- Les **risques** et l'**eau** réutilisent la mesure du score d'investissement
  (`services/scoring.py`) au lieu d'en écrire une seconde : deux barèmes pour un même
  aléa finissent par diverger, et personne ne voit l'écart. 27 % des communes du set ont
  une eau relevée **non conforme**.

Effet mesuré sur les 2 873 biens du set : **+1,3 point en moyenne** (médiane +1,0), de
−8,2 à +14,1. Les plus pénalisés sont des DPE G en zone d'avalanche et d'inondation avec
une eau non conforme — c'est-à-dire ce que ces critères servent à voir. Les seuils de
pépites (`--pepites 1:78.5`) sont calibrés sur l'ancienne échelle : **+1,3 point les
desserre légèrement**, à recalibrer au prochain resserrage.

## 4 bis. L'espace modulable en dortoir

Ajouté le 5 septembre 2026 au set têtard, poids **3**. Ce qu'il mesure : les volumes
qu'une annonce décrit et qu'on peut convertir en couchages — grange, combles et sous-sol
aménageables, dépendance, bâtiment annexe, atelier, mezzanine, pièce à aménager.

**Ce que les autres critères ne disaient pas.** `chambres_min` compte la capacité qui
existe, `logement_compact` plafonne la maison qu'on habite. Aucun des deux ne répond à la
question du week-end où tout le monde vient : où couche-t-on dix personnes sans acheter
plus grand ? La réponse est presque toujours un volume qui ne compte pas dans la surface
habitable, et c'est ce qui empêche le double paiement. La corrélation mesurée entre le
nouveau sous-score et `surface_bati` est de **0,04** : le critère ne rachète pas ce que le
format plafonne. Avec « authentique » elle est de 0,28, une grange venant souvent avec de
la vieille pierre sans que l'un mesure l'autre.

**Le silence de l'annonce note bas au lieu de sortir du calcul.** C'est le défaut corrigé
au §5 sur les features bretonnes : une mention absente vaut `n/a`, sort du dénominateur,
et le critère ne peut alors que faire monter celui dont l'annonce a employé le mot. Ici
l'annonce lue sans volume convertible note **0,20**. Bas, parce que le groupe demande un
espace prouvé ; non nul, parce qu'un grenier existe dans beaucoup de maisons anciennes
sans que l'annonce le cite.

**Trois niveaux de preuve**, parce qu'une grange et « de beaux volumes » ne promettent pas
la même chose. Un signal fort (grange, dépendance, combles aménageables, gîte possible)
vaut 0,55 ; un volume réel dont rien ne dit qu'on peut y dormir (atelier, mezzanine,
combles nus) 0,30 ; une tournure d'agence (« beaux volumes », « grande pièce ») 0,12, qui
ne peut rien emporter seule. Un seul signal fort donne 0,64 : une grange citée en passant
n'est pas un dortoir, une grange avec des combles aménageables, si. « Combles perdus » et
« non aménageable » neutralisent les signaux nus. Un signal fort **absorbe** le signal nu
qu'il contient — sans quoi « combles aménageables », qui déclenche aussi « combles »,
vaudrait 0,85 et des combles pèseraient plus qu'une grange. La cave est volontairement
absente : citée par 31 % des annonces, elle ne se transforme pas en chambre.

Mesuré le 5 septembre 2026 sur les 3 831 biens du set dans la zone (caches seuls, sans
appel réseau : avec l'IGN en direct, deux passes ne donnaient pas le même chiffre) :

| | Mesuré |
|---|---|
| Couverture | **100 %** des annonces (le texte suffit, aucun enrichissement requis) |
| Moyenne / écart-type sur le catalogue | 0,45 / **0,30** |
| Moyenne / écart-type **au-dessus de 70** | 0,84 / **0,22** |
| Effet sur le score | médiane **−1,4 point**, de −4,4 à +6,6 |
| Biens au-dessus de 70 | 196 → **216** |
| Haut du panier | **4 des 20 premiers** changent |

L'écart-type au-dessus de 70 est ce qui décide du poids, et il place ce critère au milieu
du tableau : 0,22, contre 0,43 pour le bruit et 0,10 pour le jardin. Il départage donc
plus que les critères devenus des paliers déguisés (`chambres_min` et le format sont à
0,17, `jardin` à 0,10) et moins que ce qui mène le classement. À poids 3, son pouvoir de
discrimination (poids × écart-type) vaut 0,65, à égalité avec « peu de travaux » et juste
sous « village vivant » (0,70). C'est la place que la mesure lui donne. Il ne monte pas à
4 : il déplacerait alors cinq des vingt premiers, pour une préférence que le groupe n'a
classée ni au-dessus du prix ni au-dessus de la montagne.

**L'effet sur le panier va dans l'autre sens que la médiane.** Celle-ci baisse de 1,4
point, parce que 48 % du catalogue n'a aucun volume à montrer et reste au socle. En haut,
l'effet s'inverse : les biens qui ont une grange gagnent jusqu'à 6,6 points, et au seuil
de 75,5 le panier passe de **14 à 17 biens**. Vérifier la distribution avant de republier
(la commande est dans `docs/OPERATIONS.md` §5) plutôt que de supposer le sens de l'effet.

Barème : `backend/app/services/modulable.py`. La détection tourne à l'export, comme
`pavillon_neuf` : elle ne dépend pas de la date d'enrichissement d'une ligne, donc ajouter
un mot au registre re-note tout le catalogue au ré-export suivant.

## 5. La normalisation appliquée

Règle retenue : **le poids d'un critère doit refléter ce qu'il départage ENCORE une fois
le palier passé.** Mesuré sur les 597 biens du set têtard au-dessus de 70 — le vivier où
se choisit une pépite, et non le catalogue entier où tout se mélange.

### Le poids rendu à ce qui classe (set têtard)

| Critère | Poids | σ au-dessus de 70 | Moyenne | Pourquoi |
|---|---|---|---|---|
| Chambres minimum | 4 → **2** | 0,16 | 0,92 | Le palier « capacité prouvée » (75) rend déjà le minimum non négociable |
| Format du logement | 4 → **2** | 0,13 | 0,96 | Le palier « format » (85) ferme la porte aux maisons immenses |
| Jardin | 4 → **2** | 0,11 | 0,96 | Le palier « jardin requis » (70) fait tout le travail |
| Peu de travaux | 4 → **3** | 0,17 | 0,89 | Palier (70) ; la note redevient discriminante (voir plus bas) |
| Calme (lu dans l'annonce) | 3 → **2** | 0,12 | 0,54 | `nuisance_sonore` MESURE la même chose, σ 0,43 |
| Station de ski | 2 → **1** | 0,29 | 0,52 | Corrèle à 0,80 avec l'attractivité saisonnière : le ski pesait 6 |

Ces critères n'ont pas disparu — ce qu'ils exigent est porté par les **paliers**, qui sont
absolus là où un poids est rattrapable. Ce qu'ils pesaient en plus ne classait personne :
il gonflait tous les scores.

### Deux mesures réparées

**Randonnées** : le critère notait 1,00 dès qu'un sentier existait — 94 % des biens à
égalité, σ 0,02. Il ne départageait rien **et satisfaisait à lui seul le palier « nature
ou montagne avérée », qui ne plafonnait donc jamais rien.** La donnée était pourtant là :
1 à 300 sentiers relevés autour du bien, médiane 88. Le critère lit maintenant la
**densité** entre deux repères réglables par set (10 → 200 en montagne, 20 → 250 sur le
littoral, où le GR34 fait grimper les comptages).

**Volume de travaux** : « à rénover » valait **0,85** sur un critère intitulé « peu de
travaux, rénovation complète non » — presque autant qu'une maison habitable. Pire, le
palier exigeait exactement 0,85 : les 20 % de biens rangés en « à rénover » passaient à la
virgule près, et la moindre erreur de classement traversait le filtre (une ruine notée 1★
par le groupe y est passée avec 0,85). On sépare les deux rôles : l'admissibilité ne bouge
pas (le palier descend à 0,6, « à rénover » passe toujours), la note devient honnête —
**0,65**, parce qu'une rénovation coûte.

### Le set breton : six critères qui ne classaient rien

| Critère | Poids | Mesuré sur | σ | |
|---|---|---|---|---|
| Bord de mer (mention annonce) | 5 → **1** | 3 % | 0,00 | `distance_mer` mesure la même chose sur 100 % |
| Bord d'eau (mention annonce) | 4 → **1** | 0 % | — | Le mot n'apparaît jamais |
| Vue (mention annonce) | 4 → **2** | 9 % | 0,00 | |
| En hauteur (mention annonce) | 2 → **1** | 2 % | 0,00 | `en_hauteur_geo` mesure la proéminence sur 100 % |
| Charme / cachet | 3 → **2** | 20 % | 0,00 | |
| Sans vis-à-vis | 3 → **1** | 5 % | 0,32 | Même lecture-dans-l'annonce ; l'absence de mention vaut n/a |
| Avec terrain | 3 → **3** | 89 % | 0,00 | Notait 1,00 dès un m² : seuil ajouté à 500 m² |

Après correction, il ne reste **aucun** critère à écart-type nul pesant plus de 2, sur
aucun des trois sets — et le set têtard n'en a plus un seul.

Un critère `feature` est présent ou absent : quand il est absent il vaut `n/a`, donc il
sort du dénominateur. Il ne peut donc **que** faire monter celui dont l'annonce a employé
le mot. Parmi les 19 biens réellement au bord de l'eau d'après la mesure, **3 seulement le
disent dans leur annonce** : à poids 5, le vocabulaire de l'agence pesait plus que la
distance au trait de côte. Ces critères restent — une annonce qui le dit est un signal —
mais à 1 ou 2.

### Ce que ça change, et le seuil recalibré

Sur les 2 873 biens du set : **−5,9 points de médiane** (de −14,0 à +0,4), et **8 des
20 premiers changent**. La baisse est mécanique : on a retiré ce que des critères
quasi-constants ajoutaient à tout le monde. Le classement, lui, dit maintenant autre chose.

Conséquence directe : le seuil des pépites doit suivre, sinon le site ne publie plus rien
(2 biens au-dessus de 78,5 contre 9 avant). **78,5 → 75,5**, ce qui redonne une quinzaine
de pépites ; le plancher des témoins de massif passe de 70 à 65 pour la même raison.

### Une question laissée ouverte : les ancres du contraste

`_ANCRE_BASSE = 0,20` / `_ANCRE_HAUTE = 0,90` prétendent encadrer « la moyenne pondérée
réellement atteignable ». Mesuré : sur le set têtard elle va de **0,44 à 0,81**, sur le
littoral de 0,40 à 0,81 — la borne haute n'est jamais approchée, la borne basse jamais
frôlée. Le score n'utilise donc que la plage 34–90. Mais le set Pauline, lui, monte à
0,90 : une même paire d'ancres ne peut pas coller aux trois. Des ancres **par set**
seraient le bon design ; c'est une décision de groupe, pas une correction, donc rien n'a
été touché.

## 6. Ce qui n'a pas été ajouté, et pourquoi

- **`population_jeune` / `orientation_gauche`** : les kinds existent, mais `age_median` et
  `part_gauche` sont renseignés sur **0 %** des biens — le CSV socio n'est pas peuplé
  (`scripts/build_socio_dataset.py`). Un critère toujours `pending` est une case vide.
- **Baisse de prix** : constatée sur 2 % des biens. Trop rare pour peser.
- **Nuisances de proximité** : 14 % — et déjà lues par `tranquillite`.

## 7. Les paliers retirés (5 septembre 2026)

Un **palier** plafonnait le score tant qu'une exigence du set n'était pas remplie : « pas
de jardin prouvé, tu ne dépasses pas 70 ». Neuf sur le set têtard, un sur le littoral.
Ils ont été supprimés. Trois mesures ont emporté la décision.

**Ils écrasaient le classement.** 117 biens exactement à 70,0 sur le set — et **300** sous
un profil « montagne ». Le haut de la liste était un mur de scores identiques, où aucune
pondération ne pouvait plus rien départager.

**Ils ne protégeaient que le set.** Sous les poids de quelqu'un d'autre, le profil montagne
avait déjà 26 biens sous le plancher de prix et 4 ruines dans son top 50 : les paliers ne
filtraient pas ce classement-là, ils se contentaient de l'aplatir. Tant qu'ils étaient là,
personne ne pouvait avoir un classement vraiment différent de celui du set.

**Ce que la suppression rend possible**, mesuré sur cinq profils tranchés :

| | avant | après |
|---|---|---|
| Rang déplacé par un profil | 116 à 167 places | **594 à 929 places** |
| Top 10 conservé | 1-2 sur 10 | **0-2 sur 10** |
| Amplitude d'un même bien | 3 → 100 | **0 → 100** |
| Plus gros paquet à une même valeur | 117 biens | **23 biens** |
| Valeurs distinctes dans le top 300 | 58 | **105** |

### Ce qui reprend leur travail

Un palier faisait deux choses très différentes. Chacune est reprise là où elle a sa place.

**1. Empêcher un bien mal mesuré de monter par accident.** C'était le rôle des paliers
« attractivité mesurée » et « rapport qualité/prix mesuré ». `evaluate` renormalisait sur
les critères notés, donc ne pas être mesuré faisait monter. Remplacé par l'**a priori** :
un critère non mesuré compte désormais à la **moyenne du catalogue**, calculée à l'export
et exportée avec le critère (`preferences[].apriori`). L'inconnu vaut la moyenne — ni le
bénéfice du doute, ni une condamnation.

**2. Dire « hors budget, c'est non ».** Repris à deux endroits :

- la **note du critère** : le budget tombe à zéro dès **+15 %** de dépassement (contre
  +33 % avant), les gros travaux valent 0,4 et une ruine 0,1. Chacun peut pondérer ce
  critère, ou poser son propre plafond — ce qu'un palier de set interdisait ;
- l'**appartenance au set** : au-delà de 300 k€ (le budget + 20 %), un bien n'est plus
  « du set » têtard. Ce n'est pas une note, c'est une frontière, au même titre que la zone
  géographique. Elle existe parce que la base est partagée : 348 biens venus d'autres
  collectes, jusqu'à 440 000 €, entraient dans têtard et se hissaient dans le haut du
  classement en marquant sur les vingt-six autres critères.

### Ce que ça coûte, honnêtement

Le top 50 du set contient maintenant 5 biens entre 250 et 300 k€, 6 sous le plancher de
180 k€, 2 en gros travaux et **15 dont l'état n'est pas renseigné**. Avant, les paliers en
laissaient passer zéro. C'est le prix d'un classement continu, et chacun peut le corriger
pour lui — sauf sur un point : **rien ne permet aujourd'hui de dire « je ne veux pas des
biens dont l'état est inconnu »**. L'a priori les met à la moyenne (0,80, parce que les
annonces qui parlent de l'état parlent surtout des biens en bon état) ; un malus
d'incertitude a été testé et ne change presque rien (15 → 12 biens). C'est une lacune
connue, pas un oubli.
