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

Un seuil personnel joue aussi sur les **paliers du set** : dire « mon budget est 120 k€ »
ramène bien un bien à 250 k€ sous le palier « dans le budget », au lieu de le pénaliser
d'un cran qu'il rattraperait ailleurs.

Le panneau ⚖️ affiche enfin **ce que chacun demande** — « chambres min : Léo 5, Max 2 » —
un désaccord que le poids seul ne peut pas exprimer.

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

`evaluate` renormalise sur les seuls critères mesurés : **un bien dont l'exposition n'a
jamais été calculée n'est pas pénalisé, il est jugé sans elle.** Les paliers d'exigences
(`EXIGENCES` dans `collect_tetard.py`) ferment cette porte en tête de classement — c'est
exactement leur raison d'être — mais pas en dessous de 70/78/85.

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

## 5. Ce qui n'a pas été ajouté, et pourquoi

- **`population_jeune` / `orientation_gauche`** : les kinds existent, mais `age_median` et
  `part_gauche` sont renseignés sur **0 %** des biens — le CSV socio n'est pas peuplé
  (`scripts/build_socio_dataset.py`). Un critère toujours `pending` est une case vide.
- **Baisse de prix** : constatée sur 2 % des biens. Trop rare pour peser.
- **Nuisances de proximité** : 14 % — et déjà lues par `tranquillite`.
