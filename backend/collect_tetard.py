"""Set « têtard » (id 1) : maison de retrait entre copains.

Refonte des critères + collecte, dans l'esprit de ce qui a été fait sur le littoral
breton. Ce que le groupe a tranché, et ce que le score en fait :

- « 3/4 chambres » -> capacité d'accueil mesurée sur TOUTES les annonces (repli sur les
  pièces, puis sur la surface) et exigée au-dessus de 75. Sans ce repli le critère était
  `n/a` sur la moitié du lot, donc neutre : une maison d'une seule pièce était deuxième.
- « le rapport qualité/prix c'est essentiel » -> nouveau critère au poids le plus fort :
  le prix au m² du bien contre celui des ventes du secteur (DVF).
- « l'accès à la nature/montagne grand OUI, mais pas isolé » -> nature et relief au poids
  fort, isolement retiré du critère de tranquillité, commerces/services remontés.
- « pas le bâti ancien, ça veut dire travaux » -> « peu de travaux » monte à 4, le critère
  « authentique / cachet » sort (il ne servait que de bonus : 1,00 quand cité, n/a sinon).
- budget ramené de 600 k€ à 450 k€.

Dernier tour de table (30 août), et ce qu'il change ici :

- « l'exposition / la durée d'ensoleillement » -> nouveau critère `ensoleillement`, MESURÉ
  sur le relief (heures de soleil direct au 21 décembre, orientation et pente du versant,
  cf. `app/services/soleil.py`). En vallée alpine c'est le critère que l'annonce ne donne
  pas : mesuré sur les pivots, le fond des gorges de l'Arly reçoit 0 h de soleil le 21
  décembre quand l'adret de Maurienne en reçoit 6 — même altitude, même prix au m².
- « pas des maisons immenses », précisé ensuite en « 5 chambres ça reste ok, mais qu'on ne
  survalorise pas les biens grands : un petit 3 chambres bien placé vaut mieux qu'un grand
  mal placé ». Le critère de capacité n'avait qu'un plancher — les quatorze pépites
  publiées vont jusqu'à 7 chambres pour 268 m² (Ambérieu, 299 k€). `logement_compact`
  ajoute un plafond qui ne récompense jamais le grand et ne décote que l'immense (3 et 5
  chambres à égalité, la pente commence à 6), un palier le rend ferme en tête de
  classement, et `surface_habitable` — dernier endroit où la taille payait pour elle-même
  — retombe au poids 1.
- « budget max 250 k » -> plafond ramené de 300 k€ à 250 k€ (collecte ET critère).
- « jardin requis » -> critère `jardin` + palier : un bien qui ne prouve pas d'extérieur
  ne dépasse plus 70, là où `has_terrain` (≥ 1 000 m²) restait un simple souhait.
- « un peu de travaux possible mais pas rénovation complète » -> le palier travaux
  s'ouvre à « à rénover » (0,85) et se ferme sur les gros travaux et les ruines.
- « la zone autour d'Albertville, le Beaufortain » -> trois pivots de plus, et le critère
  de temps d'accès passe à 4h30 (voir PIVOTS).

Usage :
    python collect_tetard.py --rescore-only     # met à jour les sets, re-score, ré-exporte
    python collect_tetard.py                    # collecte bienici autour des pivots + tout ça
    python collect_tetard.py --pivot diois      # un seul pivot
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.db import SessionLocal, init_db
from app.enrichment import enrich_listing
from app.models import FilterSet, Listing
from app.schemas import SearchCriteria
from app.seed import seed_from_data_json, seed_if_empty
from app.services.search import upsert_listing
from app.sources.bienici import BienIciSource

SET_ID = 1
SET_NAME = "têtard"
SET_DESC = ("Maison de retrait entre copains — Alpes et Préalpes, à l'EST de l'axe "
            "Lyon-Valence (Isère, Savoie dont Albertville et le Beaufortain, "
            "Haute-Savoie, Hautes-Alpes, Bugey, Diois et Vercors), à moins de 4h30 "
            "porte-à-porte de Paris. Pépite : bon rapport qualité/prix, 3 à 4 chambres "
            "(pas une maison immense), jardin, un peu de travaux possible mais pas une "
            "rénovation complète, bien exposée (soleil d'hiver mesuré), la montagne à la "
            "porte — mais un village vivant autour. ≤ 250 k€.")

PRIX_MAX = 250_000
# « Pas de secret : quand un bien est à 100 ou 150 k€, c'est qu'il y a un problème ;
# a priori on n'aura rien qui nous intéresse en dessous de 180 k€. » (1er septembre)
#
# Le plancher n'est pas un filtre de collecte — on continue de ramasser sous 180 k€,
# parce que le meilleur bien d'un massif pauvre reste une information (cf. ZONES). Il
# agit à deux endroits : le critère budget cesse de récompenser le bon marché, et un
# palier ferme le haut du classement.
PRIX_MIN = 180_000

# Pondérations 1-5. Deux critères mènent le classement : ce que le bien vaut pour son prix,
# et ce qu'on a devant la porte.
PREFERENCES = [
    {"kind": "rapport_qualite_prix", "weight": 5, "label": "Rapport qualité/prix (vs prix du secteur)",
     "params": {"bon": 0.75, "cher": 1.7}},
    # Une FOURCHETTE, et non un plafond. Le critère notait 1,0 tout ce qui passait sous
    # 70 % du budget : un mobil-home à 75 000 € y marquait autant qu'une maison à
    # 175 000 €, et le bon marché devenait un avantage. En dessous du plancher la note
    # redescend jusqu'à 0,15 à mi-plancher (90 k€) — fort, mais non nul : c'est un
    # a priori, pas une preuve.
    {"kind": "budget", "weight": 4, "label": "Prix entre 180 000 € et 250 000 €",
     "params": {"budget_max": PRIX_MAX, "budget_min": PRIX_MIN}},
    # `m2_min_par_piece` : le garde-fou du repli « pièces - 1 ». Une annonce peut
    # annoncer 4 pièces dans 35 m² — c'est ce qui a fait entrer un mobil-home de camping
    # dans les pépites avec « 3 chambres estimées ».
    # Poids rendu à 4 le 5 septembre 2026. Il avait été baissé au motif que le PALIER
    # portait l'exigence — les paliers ont été retirés depuis, et ce critère s'est
    # retrouvé à la fois peu pondéré et sans garde-fou. Son écart-type reste faible en
    # haut de classement (la plupart des candidats le remplissent) : ce poids ne sert pas
    # à départager, il sert à faire payer les rares qui échouent.
    {"kind": "chambres_min", "weight": 4, "label": "3 chambres minimum",
     "params": {"min": 3, "m2_min_par_piece": 20}},
    # Le pendant du plancher de chambres. Ce que le groupe a précisé le 30 août : « 5
    # chambres ça reste ok, mais je ne veux pas qu'on survalorise les biens grands — un
    # bien plus petit avec 3 chambres, bien placé, vaut mieux qu'un grand mal placé. »
    #
    # D'où la forme du critère : il ne récompense JAMAIS le grand, il décote seulement
    # l'immense. 3 et 4 chambres se valent (1,0), 5 décote à peine (0,75), et la pente ne
    # mord qu'ensuite — 6 chambres 0,375, 7 chambres 0,19. Un 95 m² de 3 chambres part
    # donc à égalité avec un 150 m² de 4, et ce sont l'exposition, la nature et le prix
    # au m² qui les départagent : c'est exactement ce qui a été demandé.
    # (`m2_ok`/`m2_max` : le repli quand l'annonce ne donne pas les chambres. Calé pour
    # dire la même chose que le barème par chambres — 170 m² pleins, 190 m² à 0,85.)
    # Poids rendu à 4 le 5 septembre 2026. Il avait été baissé au motif que le PALIER
    # portait l'exigence — les paliers ont été retirés depuis, et ce critère s'est
    # retrouvé à la fois peu pondéré et sans garde-fou. Son écart-type reste faible en
    # haut de classement (la plupart des candidats le remplissent) : ce poids ne sert pas
    # à départager, il sert à faire payer les rares qui échouent.
    {"kind": "logement_compact", "weight": 4, "label": "Format maison de retrait (3 à 5 chambres, pas immense)",
     "params": {"ideal": 4, "max": 5, "m2_ok": 170, "m2_max": 300}},
    # Le contrepoids du format, et la question que `chambres_min` ne pose pas : les
    # chambres comptent la capacité EXISTANTE, celui-ci compte celle qu'on peut se donner.
    # Une grange attenante, des combles aménageables, une dépendance — de quoi faire une
    # chambre commune pour le week-end où tout le monde vient, sans acheter plus grand.
    #
    # Les deux ne se paient pas deux fois : ces volumes ne comptent pas dans la surface
    # habitable (corrélation mesurée avec `surface_bati` : 0,04), donc ce critère ne
    # rachète pas ce que `logement_compact` plafonne. Barème dans `services/modulable.py`.
    #
    # Poids 3, et non 4 : sur les 216 biens du set au-dessus de 70 — le vivier où se
    # choisit une pépite — son écart-type est de 0,22, contre 0,17 pour les chambres et
    # le format et 0,10 pour le jardin, devenus des paliers déguisés en poids. Il
    # départage donc encore une fois le palier passé, sans mener le classement : son
    # pouvoir de discrimination (poids × écart-type) le range au milieu du tableau, juste
    # sous « peu de travaux ». À 4, il déplacerait cinq des vingt premiers pour une
    # préférence que le groupe n'a pas classée au-dessus du prix ni de la montagne.
    {"kind": "espace_modulable", "weight": 3,
     "label": "Espace modulable en dortoir (grange, combles, dépendance)", "params": {}},
    # « Un peu de travaux possible, mais pas une rénovation complète » : le critère garde
    # sa forme (habitable 1,0 · à rafraîchir 1,0 · à rénover 0,85 · gros travaux 0,4 ·
    # ruine 0,1), c'est le PALIER qui s'ouvre d'un cran, à 0,85.
    # Poids rendu à 4 : le palier travaux a été retiré, et c'est désormais la seule
    # chose qui distingue une maison habitable d'une grange à aménager. Mesuré sur la
    # grange de Saint-Andéol (« prévoir beaucoup de travaux pour aménager en habitation »,
    # sortie 1re à 94/100 tant qu'elle passait pour habitable) : à poids 3 elle reste 17e,
    # à poids 4 elle descend encore. Barème : habitable 1,0 · à rafraîchir 1,0 ·
    # à rénover 0,65 · gros travaux 0,4 · ruine 0,1.
    # `min_etat` : le set le dit dans sa propre description — « un peu de travaux possible
    # mais pas une rénovation complète ». En dessous de « à rénover », la note du bien est
    # divisée par quatre. Ce n'est PAS un plafond : le bien garde son classement, il paie
    # ce qu'il coûte, et qui veut un chantier n'a qu'à baisser ce seuil pour lui.
    {"kind": "light_works", "weight": 4, "label": "Peu de travaux (rafraîchir oui, rénovation complète non)",
     "params": {"min_etat": "renover"}},
    # Le DPE dit ce que `light_works` ne dit pas : `light_works` lit l'état du bâti dans
    # l'annonce (habitable / à rafraîchir / à rénover), le DPE lit ce que le bien coûtera
    # à chauffer. Une maison « habitable » classée G est habitable ET une passoire : deux
    # informations, et une seule était lue. Renseigné par 81 % des annonces du set, soit
    # mieux que l'état du bâti (59 %). Poids 3 : cher à corriger, mais corrigeable — et
    # en montagne un G se chauffe au bois depuis toujours.
    {"kind": "dpe", "weight": 3, "label": "Performance énergétique (DPE)", "params": {}},
    # Les aléas de la commune (Géorisques), pondérés par leur gravité pour un logement.
    # C'était déjà mesuré — mais seulement dans le score d'INVESTISSEMENT, que le groupe
    # ne regarde pas pour choisir : le match du set ne disait rien d'une maison en zone
    # inondable ou en mouvement de terrain. En montagne, avalanche et mouvement de
    # terrain ne sont pas des cases administratives.
    {"kind": "risques_naturels", "weight": 3, "label": "Risques naturels (inondation, mouvement de terrain…)", "params": {}},
    # L'eau du robinet (Hub'Eau : pesticides, nitrates, PFAS, conformité). Même histoire :
    # mesurée sur 82 % des biens, non conforme sur 27 % d'entre eux, et invisible dans le
    # classement. Poids 2 : ça se corrige avec un filtre, ça ne se corrige pas avec un
    # filtre quand c'est la nappe.
    {"kind": "qualite_eau", "weight": 2, "label": "Qualité de l'eau (réseau)", "params": {}},
    # Exposition et durée d'ensoleillement, MESURÉES sur le relief (pas lues dans
    # l'annonce, qui écrit « plein sud » sans jamais l'avoir vérifié) : heures de soleil
    # direct au solstice d'hiver, orientation et pente du versant. Poids 4 : en montagne,
    # l'écart entre deux biens du même village est un hiver entier — 0 h au fond des
    # gorges de l'Arly contre 6 h sur l'adret de Maurienne, à altitude égale.
    {"kind": "ensoleillement", "weight": 4, "label": "Exposition / soleil d'hiver",
     "params": {"heures_faibles": 1.5, "heures_bonnes": 6.0}},
    # « Jardin requis » : un PLANCHER, distinct du souhait de grand terrain ci-dessous.
    # Seuil bas (300 m²), et une annonce qui décrit un extérieur sans en donner la surface
    # note 0,7 — assez pour passer le palier, pas assez pour valoir un jardin mesuré.
    # Poids rendu à 4 le 5 septembre 2026. Il avait été baissé au motif que le PALIER
    # portait l'exigence — les paliers ont été retirés depuis, et ce critère s'est
    # retrouvé à la fois peu pondéré et sans garde-fou. Son écart-type reste faible en
    # haut de classement (la plupart des candidats le remplissent) : ce poids ne sert pas
    # à départager, il sert à faire payer les rares qui échouent.
    {"kind": "jardin", "weight": 4, "label": "Jardin (requis)", "params": {"min_surface": 300}},
    # « L'attractivité Airbnb comme un critère important » (31 août). Poids 4 : le rang
    # des critères qui décident sans dominer — budget, travaux, jardin, ensoleillement —
    # et non 5, réservé au rapport qualité/prix et au relief. Une maison de retrait entre
    # copains passe l'essentiel de l'année vide ; ce qu'elle peut se louer les semaines
    # où personne n'y va change son coût réel, et c'est le seul critère du set qui parle
    # d'argent qui rentre plutôt que d'argent qui sort.
    #
    # MESURÉE (`services/tourisme.py`), et non lue dans l'annonce : remontée mécanique
    # (l'hiver), lac et sites (l'été), hébergement touristique déjà installé (le marché
    # existe), restaurants (ce qu'il faut sur place). Mesuré sur les pivots :
    # Chamonix 1,00 · Aix-les-Bains 1,00 · Beaufort 0,92 · Barcelonnette 0,80 ·
    # Jarrier 0,78 · Die 0,71 · Hauteville-Lompnes 0,23.
    #
    # Demande le cache réchauffé (`scripts/warm_tourisme.py`), sans quoi le critère sort
    # en `pending` — donc EXCLU du score au lieu de le baisser, et rien ne le dit.
    {"kind": "attractivite_airbnb", "weight": 4, "label": "Attractivité locative saisonnière (Airbnb)",
     "params": {}},
    {"kind": "coin_nature", "weight": 4, "label": "Coin de nature (eau, bois, vue dégagée)",
     "params": {"alt_min": 400, "alt_ref": 900}},
    # Relief monté à 5 : le groupe a demandé les Alpes après un premier jeu entièrement
    # posé autour de Saint-Étienne. La collecte y va désormais, mais encore faut-il que
    # le score valorise l'altitude qu'elle ramène.
    {"kind": "relief_mountain", "weight": 5, "label": "Montagne / relief", "params": {"ref_altitude": 800}},
    # Charme remonté à 4 : « pas de charme » revient trois fois en reproche (1★),
    # « charme de la bâtisse » une fois en éloge (4★).
    {"kind": "cachet", "weight": 4, "label": "Cachet (caractère, pas de pavillon)", "params": {}},
    # Le terrain était monté à 4 sur « pas de terrain » (1★, deux fois), « peu de
    # terrain », « le terrain est petit ». Ce reproche est désormais porté par `jardin`,
    # qui l'exige au lieu de le souhaiter ; il ne reste ici que la préférence pour le
    # GRAND terrain, à 3 — sinon le même reproche pèserait deux fois.
    {"kind": "has_terrain", "weight": 3, "label": "Grand terrain (≥ 1 000 m²)", "params": {"min_surface": 1000}},
    # `village_vivant` remplace `commerces`, qui saturait : deux biens notés 1★ « trop en
    # ville » marquaient pourtant le maximum sur ce critère.
    {"kind": "village_vivant", "weight": 3, "label": "Village vivant (ni désert, ni ville)",
     "params": {"vivant": 8, "ideal": 25, "ville": 120}},
    # Le critère notait 1,00 pour 94 % des biens (écart-type 0,02) : il ne départageait
    # personne et satisfaisait à lui seul le palier « nature ou montagne avérée », qui ne
    # plafonnait donc jamais rien. La donnée existe pourtant — 1 à 300 sentiers relevés,
    # médiane 88. Le critère lit maintenant la DENSITÉ, entre les déciles mesurés.
    {"kind": "hiking", "weight": 3, "label": "Randonnées au départ",
     "params": {"peu": 10, "beaucoup": 200}},
    # 4h30 et non 4h : le groupe a demandé le Beaufortain, mesuré à 4h10 porte-à-porte
    # (Val d'Arly 4h06 ; Albertville, elle, passe à 3h57). Laisser le plafond à 4h aurait
    # donné 0 sur ce critère à la moitié de la zone qu'on vient d'ajouter — collecter une
    # zone puis la noter zéro n'a pas de sens. Le barème reste décroissant et continue de
    # préférer le proche : 0,60 à 3h, 0,22 à 3h57, 0,13 à 4h10.
    {"kind": "temps_acces", "weight": 3, "label": "≤ 4h30 porte-à-porte depuis Paris",
     "params": {"max_minutes": 270}},
    # Isolement neutralisé : le groupe veut le calme, pas le bout du monde.
    # Poids 2 : ce critère LIT le calme dans l'annonce (écart-type 0,12) quand
    # `nuisance_sonore` le MESURE sur les routes et les voies ferrées (écart-type 0,43).
    # Les deux disent la même chose ; on paie surtout celui qui la mesure.
    {"kind": "tranquillite", "weight": 2, "label": "Calme, sans vis-à-vis, hors lotissement",
     "params": {"poids_isolement": 0, "poids_densite": 0}},
    # Bruit monté à 3, et il compte désormais les routes passantes : deux biens notés 1★
    # « le long d'une route nationale », que le critère ne voyait pas.
    {"kind": "nuisance_sonore", "weight": 3, "label": "Loin d'une route passante / autoroute / rail",
     "params": {"min_m": 200, "ref_m": 1000, "poids_route": 0.45}},
    # Descendu de 2 à 1, et le seuil de 100 à 90 m² : c'était le dernier endroit où la
    # taille était récompensée pour elle-même, alors que la capacité d'accueil est déjà
    # mesurée par `chambres_min`. « Un bien plus petit avec 3 chambres, bien placé, vaut
    # mieux qu'un grand mal placé » — à poids 2 sur un seuil de 100 m², un 95 m² de trois
    # chambres perdait des points qu'aucun critère de placement ne lui rendait.
    {"kind": "surface_habitable", "weight": 1, "label": "≥ 90 m² habitables", "params": {"min": 90}},
    {"kind": "near_gare", "weight": 2, "label": "Proche d'une gare", "params": {"max_km": 15}},
    {"kind": "fiber", "weight": 2, "label": "Fibre (télétravail)", "params": {}},
    # Poids 1 : l'attractivité saisonnière (poids 4) est calculée à 80 % sur la présence
    # d'une remontée mécanique — c'est ce que ce critère mesure. Corrélation 0,80 entre
    # les deux : à 2 + 4, le ski pesait 6 dans un set où le maximum est 5. Il reste à 1
    # parce qu'il est relevé sur 98 % des biens quand l'attractivité ne l'est que sur 47 %.
    {"kind": "ski", "weight": 1, "label": "Station de ski à proximité", "params": {"max_km": 30}},
    {"kind": "near_city", "weight": 2, "label": "Accessible depuis Marseille",
     "params": {"ville": "Marseille", "max_km": 300}},
    {"kind": "near_corridor", "weight": 1, "label": "Axe Paris-Marseille",
     "params": {"villes": ["Paris", "Marseille"], "max_km": 40}},
]

# Paliers : au-delà d'un certain score, un critère cesse d'être optionnel.
#
# `evaluate` renormalise sur les seuls critères mesurés. Un bien dont peu de critères sont
# notés est donc jugé sur ceux-là, et peut monter très haut sans qu'on sache combien de
# monde il loge. C'est exactement ce qui s'est produit : une annonce d'une seule pièce
# deuxième d'un classement qui demandait quatre chambres.
# Les PALIERS ont été retirés le 5 septembre 2026 : ils plafonnaient un bien tant qu'une
# exigence n'était pas remplie. Mesuré sur le catalogue, ils collaient 127 biens du set
# (et 300 sous un profil « montagne ») exactement à la même valeur, et ils s'appliquaient
# à la lentille de CHACUN — personne ne pouvait donc avoir un classement vraiment
# différent. Ce qu'ils exigeaient est passé dans les critères eux-mêmes : la note budget
# tombe à zéro dès +15 % de dépassement, celle des travaux à 0,4 pour de gros travaux et
# 0,1 pour une ruine, et un critère non mesuré vaut désormais la moyenne du catalogue au
# lieu de disparaître du calcul (cf. `evaluate(..., apriori=)`).


# Pivots de collecte : les parties MONTAGNE des départements déjà couverts par le set
# (26, 07, 73, 01, 43, 42). La zone ne change pas ; ce sont les points de départ qui
# quittent la vallée du Rhône, d'où venait la majorité du haut de classement précédent
# (Châteauneuf-sur-Isère, 154 m).
# Le porte-à-porte depuis Paris est mesuré, pas supposé (Vercors 3h04, Trièves 3h21,
# Chartreuse 3h29, Oisans 3h39, Aravis 3h53, Gap 3h54, Maurienne 3h59). Le Beaufortain,
# à 4h10, était écarté à ce titre : le groupe l'a redemandé, il entre, et le critère de
# temps d'accès passe à 4h30 pour ne pas le noter zéro d'office.
# À l'EST de l'axe Lyon-Valence, uniquement. Le premier jeu de pépites était posé autour
# de Saint-Étienne : le rapport qualité/prix, critère de tête, favorise mécaniquement les
# secteurs les moins chers, et l'Ardèche, la Loire et la Haute-Loire le sont. Le groupe a
# donc resserré sur les Alpes et leurs avant-pays.
#
# Le porte-à-porte depuis Paris est mesuré, pas supposé : la plupart de ces pivots sont
# sous les 4h (Vercors 3h04, Trièves 3h21, Chartreuse 3h29, Oisans 3h39, Aravis 3h53,
# Gap 3h54, Maurienne 3h59, Albertville 3h57). Beaufortain (4h10) et Val d'Arly (4h06)
# dépassent les 4h de la consigne initiale : le groupe les a demandés explicitement, ils
# sont donc collectés, et `temps_acces` les note en conséquence (0,13 à 4h10) sans les
# exclure. Chablais, haute Tarentaise et Briançonnais restent dehors, faute de demande.
PIVOTS = [
    # --- Le cœur du set : ce qui tient sous les 4h30 porte-à-porte -------------------
    # Préalpes drômoises (à l'est du Rhône)
    ("Diois / Die", 44.754, 5.370, ["26"]),
    ("Vercors drômois / La Chapelle-en-Vercors", 44.968, 5.415, ["26"]),
    # Alpes du Nord
    ("Chartreuse / Saint-Pierre", 45.335, 5.820, ["38", "73"]),
    ("Vercors isérois / Villard-de-Lans", 45.070, 5.553, ["38"]),
    ("Trièves / Mens", 44.815, 5.750, ["38"]),
    ("Matheysine / La Mure", 44.900, 5.787, ["38"]),
    ("Oisans / Bourg-d'Oisans", 45.055, 6.030, ["38"]),
    ("Grésivaudan-Belledonne / Allevard", 45.395, 6.075, ["38", "73"]),
    ("Bauges / Le Châtelard", 45.700, 6.110, ["73"]),
    ("Maurienne / Saint-Jean", 45.276, 6.352, ["73"]),
    ("Aravis-Bornes / Thônes", 45.881, 6.325, ["74"]),
    ("Dévoluy-Gapençais / Gap", 44.620, 5.995, ["05"]),
    ("Bugey / Hauteville-Lompnes", 45.980, 5.600, ["01"]),
    # Demandés par le groupe (30 août) : « la zone autour d'Albertville, dans le
    # Beaufortain par exemple ». Trois pivots plutôt qu'un, parce que les rayons de
    # collecte (8/16/25 km) autour d'Albertville seul s'arrêteraient au seuil du
    # Beaufortain — la vallée du Doron est à 20 km de la ville, le Val d'Arly à 25.
    ("Albertville / Combe de Savoie", 45.676, 6.393, ["73"]),
    ("Beaufortain / Beaufort", 45.721, 6.575, ["73"]),
    ("Val d'Arly / Flumet", 45.816, 6.517, ["73", "74"]),

    # --- Le reste des Alpes, pour la comparaison (31 août) ---------------------------
    # « Essaye de couvrir toutes les Alpes, au moins avec le meilleur bien de chaque
    # zone, même si son score est bas : ça permettra de voir la différence entre les
    # régions. » Ces foyers-là ne sont pas là pour produire des pépites — la plupart
    # sortent des 4h30 et le critère de temps d'accès les note en conséquence. Ils sont
    # là pour répondre à une question que le classement seul ne pose jamais : à budget
    # égal (250 k€), qu'est-ce qu'on a en Tarentaise, dans le Queyras, dans l'Ubaye ou
    # dans le Mercantour ? L'export publie le meilleur bien de chacun (cf. `ZONES`).
    #
    # Savoie / Haute-Savoie
    ("Lac du Bourget / Aix-les-Bains", 45.720, 5.880, ["73"]),
    ("Avant-pays savoyard / Yenne-Novalaise", 45.630, 5.750, ["73", "01"]),
    ("Tarentaise / Moûtiers", 45.484, 6.532, ["73"]),
    ("Haute-Tarentaise / Bourg-Saint-Maurice", 45.618, 6.769, ["73"]),
    ("Vanoise / Pralognan-Champagny", 45.390, 6.720, ["73"]),
    ("Annecy-Semnoz / Annecy", 45.870, 6.140, ["74"]),
    ("Faucigny-Grand Massif / Taninges", 46.090, 6.610, ["74"]),
    ("Chablais / Morzine-Abondance", 46.230, 6.680, ["74"]),
    ("Léman / Évian-Thonon", 46.390, 6.560, ["74"]),
    ("Mont-Blanc / Sallanches-Passy", 45.930, 6.640, ["74"]),
    ("Chamonix / Vallée de l'Arve", 45.923, 6.869, ["74"]),
    # Hautes-Alpes
    ("Briançonnais-Écrins / L'Argentière", 44.850, 6.600, ["05"]),
    ("Queyras / Guillestre", 44.700, 6.740, ["05"]),
    ("Champsaur-Valgaudemar / Orcières", 44.680, 6.190, ["05"]),
    ("Embrunais-Serre-Ponçon / Embrun", 44.565, 6.495, ["05"]),
    ("Laragne-Serres / Buëch", 44.320, 5.800, ["05"]),
    # Drôme provençale
    ("Baronnies / Nyons-Buis", 44.280, 5.270, ["26"]),
    # Alpes du Sud
    ("Ubaye / Barcelonnette", 44.388, 6.652, ["04"]),
    ("Haute-Provence / Digne", 44.092, 6.236, ["04"]),
    ("Verdon / Castellane-Moustiers", 43.847, 6.510, ["04"]),
    ("Lure-Forcalquier / Forcalquier", 43.960, 5.780, ["04"]),
    ("Mercantour-Vésubie / Saint-Martin", 44.070, 7.250, ["06"]),
    ("Alpes d'Azur / Guillaumes-Puget", 44.000, 6.900, ["06"]),
]

# Les zones de comparaison, dérivées des pivots : chaque bien est rattaché au pivot le
# plus proche (partition de Voronoï, bornée par le rayon). Le nom de la zone est ce qui
# précède le « / » — le massif, pas la ville qui a servi de point de collecte.
#
# L'export publie le MEILLEUR bien de chaque zone même s'il est sous le seuil des
# pépites. Sans ça, le site ne montre que les Préalpes et la Maurienne — les secteurs où
# 250 k€ achètent quelque chose — et le groupe n'a aucun moyen de voir ce que le même
# budget donne (ou ne donne pas) en Tarentaise ou au bord du Léman. Un panier vide dans
# une zone est une information ; un panier absent n'en est pas une.
ZONE_RAYON_KM = 30.0
ZONES = [{"nom": nom.split(" / ")[0], "lat": lat, "lon": lon, "rayon_km": ZONE_RAYON_KM}
         for nom, lat, lon, _ in PIVOTS]



# Les sources qui alimentent le set. Bien'ici se collecte autour des pivots (géo fine) ;
# Notaires et Paruvendu ne savent chercher que par département, d'où la liste dérivée
# des pivots — elle suit automatiquement tout ajout ou retrait de massif.
SOURCES_ANNONCES = ("bienici", "notaires", "paruvendu")
DEPARTEMENTS = sorted({d.zfill(2) for _n, _la, _lo, deps in PIVOTS for d in deps})


def _collecter_portails_publics(collected: dict, existing: set,
                                departements: list[str] | None = None) -> None:
    """Notaires et Paruvendu : deux portails sans clé, sans cookie et sans anti-bot.

    Ils ne recoupent presque pas Bien'ici — mesuré sur la zone : 42 % des maisons
    notariales et 58 % des paruvendu étaient absentes de la base. L'inventaire notarial
    (successions, ventes sur adjudication, biens ruraux) ne passe pas par les portails.

    Aucun des deux ne donne de coordonnées : c'est `services.enrich.annotate` qui pose le
    centroïde communal, sans quoi le filtre « à l'est de l'axe » et l'étage altitude de
    l'entonnoir seraient inertes sur ces biens (mesuré : 182 sur 182 passaient sans être
    filtrés, et le classement montagne remontait Pierrelatte et Valence).
    """
    from app.sources.notaires import NotairesSource
    from app.sources.paruvendu import ParuvenduSource

    deps = departements or DEPARTEMENTS
    for src, nom, pages in ((NotairesSource(), "notaires", 8), (ParuvenduSource(), "paruvendu", 10)):
        neufs = vus = 0
        for dep in deps:
            for page in range(1, pages + 1):
                crit = SearchCriteria(departement=dep, property_types=["maison"],
                                      prix_max=PRIX_MAX, par_page=100, page=page)
                try:
                    items = src.search(crit).items
                except Exception as e:  # noqa: BLE001
                    # Fin de pagination, pas panne : notaires répond 400 au-delà de la
                    # dernière page, paruvendu 404. Le plafond `pages` doit rester
                    # au-dessus du nombre réel de pages, sinon c'est LUI qui tronque —
                    # et une troncature ressemble trait pour trait à un département vide.
                    if not any(code in str(e) for code in ("400", "404")):
                        print(f"  [{nom} {dep}] KO: {type(e).__name__}: {str(e)[:60]}", flush=True)
                    break
                if not items:
                    break
                for it in items:
                    vus += 1
                    cle = (it.source, it.external_id)
                    if not it.external_id or cle in existing or cle in collected:
                        continue
                    if it.prix is None or it.prix > PRIX_MAX or not it.commune:
                        continue
                    collected[cle] = it
                    neufs += 1
                time.sleep(0.4)
        print(f"  [{nom}] {neufs} neufs (sur {vus} annonces, "
              f"départements {', '.join(deps)})", flush=True)


def ensure_sets(db) -> None:
    criteria = {"property_types": ["maison"], "preferences": PREFERENCES,
                # Zones de comparaison : l'export publie le meilleur bien de chacune,
                # même sous le seuil des pépites (cf. ZONES).
                "zones": ZONES,
                # La zone appartient au set : elle se réapplique à chaque export sans
                # qu'il faille repasser sur la base, et un bien collecté à l'ouest par
                # une future recherche est écarté tout seul.
                # `prix_max_membre` : le budget du set est 250 k€ ; au-delà de 300 k€ un
                # bien n'est plus « du set » — il vient d'une autre collecte et se
                # hissait dans le classement en marquant partout ailleurs. La marge de
                # 20 % laisse la place à qui veut monter son propre plafond.
                # Ancres du contraste, PROPRES à ce set. La note finale étire la
                # moyenne pondérée entre ces deux bornes : elles disent ce que « ne rien
                # cocher » et « tout cocher » valent ICI. Mesuré le 5 septembre 2026 sur
                # 2 698 biens : la moyenne va de 0,42 à 0,79. Avec les ancres communes
                # 0,20/0,90, le set n'utilisait que 53 % de l'échelle 0-100 — et son
                # score se comparait à celui d'un autre groupe qui ne cherche pas la même
                # chose. À revoir si le set change beaucoup de critères.
                "ancres": {"basse": 0.40, "haute": 0.80},
                "zone": {"est_axe_lyon_valence": True, "prix_max_membre": PRIX_MAX * 1.2}}
    fs = db.get(FilterSet, SET_ID)
    if fs is None:
        db.add(FilterSet(id=SET_ID, name=SET_NAME, description=SET_DESC, criteria=criteria))
    else:
        fs.name, fs.description, fs.criteria = SET_NAME, SET_DESC, criteria
    db.commit()
    print(f"Set prêt : « {SET_NAME} » ({len(PREFERENCES)} critères, "
          f"{len(ZONES)} zones, ≤{PRIX_MAX // 1000}k, est du Rhône).", flush=True)


def _seuils(texte: str | None) -> dict:
    """« 1:78.5,4:80 » -> {1: 78.5, 4: 80.0}."""
    out = {}
    for morceau in (texte or "").split(","):
        if ":" in morceau:
            sid, seuil = morceau.split(":", 1)
            out[int(sid.strip())] = float(seuil.strip())
    return out


def _exporter(db, quoi: str, pepites: dict | None = None,
              meilleur_zone: dict | None = None) -> None:
    from app.services.export_static import export_to_dir

    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    print(f"\n{quoi} vers {os.path.abspath(data_dir)}...", flush=True)
    if pepites:
        print(f"  resserrage : {', '.join(f'set {k} ≥ {v:g}' for k, v in pepites.items())}", flush=True)
    if meilleur_zone:
        print(f"  témoins de zone : {', '.join(f'set {k} plancher {v:g}' for k, v in meilleur_zone.items())}",
              flush=True)
    t = time.time()
    stats = export_to_dir(db, data_dir, download_photos=True, pepites=pepites or None,
                          meilleur_par_zone=meilleur_zone or None)
    print(f"  export OK en {time.time() - t:.0f}s : {stats}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=80, help="plafond dur du nb de biens enrichis")
    ap.add_argument("--keep", type=int, default=0,
                    help="entonnoir : plafond appliqué au classement par note d'annonce "
                         "(0 = pas de plafond)")
    ap.add_argument("--min-altitude", type=float, default=250.0, dest="min_altitude",
                    help="entonnoir : écarte les communes plus basses (0 = étage sauté)")
    ap.add_argument("--pivot", help="ne collecter qu'autour des pivots dont le nom contient "
                                    "ce texte (ex. « diois »)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--dump", default="",
                    help="écrit les annonces collectées dans ce fichier JSON avant "
                         "l'entonnoir. La collecte dure ~20 min et ne vit qu'en mémoire : "
                         "une interruption la perd entièrement (vécu deux fois).")
    ap.add_argument("--collect-only", action="store_true", dest="collect_only",
                    help="s'arrête après le dump (collecte seule, ~20 min).")
    ap.add_argument("--from-dump", default="", dest="from_dump",
                    help="repart d'un dump au lieu de recollecter (entonnoir + "
                         "enrichissement seuls).")
    ap.add_argument("--departements", default="",
                    help="n'enrichir que ces départements (ex. « 38,74,05 »). L'entonnoir "
                         "classe par note d'annonce, qui favorise le rapport qualité/prix "
                         "donc les secteurs bon marché : sans ce filtre, une zone chère "
                         "n'est jamais enrichie même quand elle est collectée.")
    ap.add_argument("--est-du-rhone", action="store_true", dest="est_du_rhone",
                    help="n'enrichir que les annonces à l'EST de l'axe Lyon-Valence. "
                         "L'entonnoir classe par note d'annonce, qui favorise le rapport "
                         "qualité/prix donc les secteurs bon marché : sans ce filtre, un "
                         "dump mêlant les deux rives n'enrichit presque que l'ouest.")
    ap.add_argument("--pepites", default="",
                    help="resserrage à l'export : « 1:78.5,4:80 ». La base garde tout le "
                         "catalogue de chaque set ; sans ce filtre, l'export republie "
                         "aussi celui des AUTRES sets et annule leur resserrage.")
    ap.add_argument("--meilleur-zone", default="", dest="meilleur_zone",
                    help="publie EN PLUS le meilleur bien de chaque zone, même sous le "
                         "seuil des pépites : « 1:70 » (set:plancher). Sert à comparer "
                         "les massifs entre eux — sans ça le site ne montre que les "
                         "secteurs où le budget achète quelque chose.")
    ap.add_argument("--sans-portails-publics", action="store_true",
                    dest="sans_portails_publics",
                    help="ne collecter que Bien'ici, sans Notaires ni Paruvendu")
    ap.add_argument("--no-export", action="store_true",
                    help="ne pas exporter (le seuil des pépites se calibre après coup). "
                         "À n'utiliser que si un export suit dans la foulée : un bien "
                         "collecté mais pas exporté n'existe pas (cf. docs/OPERATIONS.md).")
    ap.add_argument("--rescore-only", action="store_true",
                    help="ne collecte rien : met à jour les sets + re-score + ré-exporte")
    ap.add_argument("--reseed", action="store_true",
                    help="RECONSTRUIT la base depuis data.json (DESTRUCTIF : efface les "
                         "biens pas encore exportés, y compris ceux d'une autre session)")
    args = ap.parse_args()

    init_db()
    # La base SQLite est partagée : un seed_from_data_json() inconditionnel efface les
    # biens qu'une autre collecte n'a pas encore exportés (cf. docs/OPERATIONS.md §0).
    if args.reseed:
        print("Reconstruction de la base depuis data.json (--reseed)...", flush=True)
        print(f"  -> {seed_from_data_json()}", flush=True)
    else:
        recap = seed_if_empty()
        print(f"Base {'seedée depuis data.json : ' + str(recap) if recap else 'déjà peuplée : seed sauté'}",
              flush=True)

    db = SessionLocal()
    ensure_sets(db)

    pepites = _seuils(args.pepites)
    meilleur_zone = _seuils(args.meilleur_zone)

    if args.rescore_only:
        _exporter(db, "Re-score seul : ré-export", pepites, meilleur_zone)
        db.close()
        print("TERMINÉ.", flush=True)
        return 0

    from dataclasses import asdict

    from app.sources.base import NormalizedListing

    # Clé (source, id) et non id seul : trois sources alimentent maintenant `collected`
    # et rien ne garantit que leurs identifiants ne se croisent pas — un id notaires est
    # un entier court, un id bienici aussi.
    existing = {(s_, e) for (s_, e) in
                db.query(Listing.source, Listing.external_id)
                .filter(Listing.source.in_(SOURCES_ANNONCES)).all()}
    collected: dict[tuple, object] = {}

    if args.from_dump:
        with open(args.from_dump, encoding="utf-8") as fh:
            brut = json.load(fh)
        for d in brut:
            it = NormalizedListing(**d)
            cle = (it.source, it.external_id)
            if it.external_id and cle not in existing:
                collected[cle] = it
        print(f"Dump relu : {len(collected)} annonces neuves sur {len(brut)} "
              f"({os.path.abspath(args.from_dump)})", flush=True)
        return _traiter(db, args, collected, pepites, meilleur_zone)

    src = BienIciSource()
    pivots = PIVOTS
    if args.pivot:
        pivots = [p for p in PIVOTS if args.pivot.lower() in p[0].lower()]
        if not pivots:
            print(f"Aucun pivot ne correspond à « {args.pivot} ». Disponibles : "
                  + ", ".join(p[0] for p in PIVOTS), flush=True)
            return 2
        print(f"Pivots retenus : {', '.join(p[0] for p in pivots)}", flush=True)

    for nom, lat, lon, depts in pivots:
        crit = SearchCriteria(property_types=["maison"], prix_max=PRIX_MAX)
        try:
            items = src.collect_around(crit, lat, lon, depts, radii=(8, 16, 25), cap=None)
        except Exception as e:  # noqa: BLE001
            print(f"  [{nom}] collecte KO: {type(e).__name__}: {str(e)[:60]}", flush=True)
            continue
        neufs = 0
        for it in items:
            cle = (it.source, it.external_id)
            if not it.external_id or cle in existing or cle in collected:
                continue
            if it.prix is not None and it.prix > PRIX_MAX:
                continue
            collected[cle] = it
            neufs += 1
        print(f"  [{nom}] {neufs} neufs (sur {len(items)} annonces)", flush=True)

    if not args.sans_portails_publics:
        # Les départements des pivots RETENUS : sinon `--pivot maurienne` limiterait
        # Bien'ici à un massif pendant que les portails ratisseraient les huit.
        deps = sorted({d.zfill(2) for _n, _la, _lo, dd in pivots for d in dd})
        _collecter_portails_publics(collected, existing, deps)

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as fh:
            json.dump([asdict(it) for it in collected.values()], fh, ensure_ascii=False)
        print(f"\nDump écrit : {len(collected)} annonces -> {os.path.abspath(args.dump)}", flush=True)

    if args.collect_only:
        db.close()
        print("Collecte seule (--collect-only) : rien d'enrichi, rien d'exporté.", flush=True)
        return 0

    return _traiter(db, args, collected, pepites, meilleur_zone)


def _traiter(db, args, collected: dict, pepites: dict, meilleur_zone: dict | None = None) -> int:
    """Entonnoir, enrichissement, mise en base, export. Séparé de la collecte pour être
    rejouable depuis un dump : la collecte dure ~20 min et ne survit pas à une coupure."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from app.models import Listing
    from app.services.entonnoir import appliquer as entonnoir

    if args.departements:
        vises = {d.strip().zfill(2) for d in args.departements.split(",") if d.strip()}
        avant = len(collected)
        collected = {k: v for k, v in collected.items()
                     if str(getattr(v, "departement", "") or "").zfill(2) in vises}
        print(f"\nFiltre départements {sorted(vises)} : {len(collected)}/{avant} annonces", flush=True)

    if args.est_du_rhone:
        from app.services.geo import est_a_lest_du_rhone
        avant = len(collected)
        collected = {k: v for k, v in collected.items()
                     if est_a_lest_du_rhone(getattr(v, "latitude", None),
                                            getattr(v, "longitude", None)) is not False}
        print(f"\nÀ l'est de l'axe Lyon-Valence : {len(collected)}/{avant} annonces", flush=True)

    print(f"\nEntonnoir sur {len(collected)} annonces :", flush=True)
    todo = entonnoir(list(collected.values()), profil="montagne",
                     min_altitude=args.min_altitude or None, prix_max=PRIX_MAX,
                     garder=args.keep or None)[: args.cap]

    print(f"\nEnrichissement de {len(todo)} biens ({args.workers} workers)...", flush=True)
    t0 = time.time()
    enriched = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(enrich_listing, it): it for it in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                enriched.append(f.result())
            except Exception as e:  # noqa: BLE001
                print(f"  enrich KO: {type(e).__name__}: {str(e)[:60]}", flush=True)
            if i % 10 == 0:
                print(f"  {i}/{len(todo)} ({time.time() - t0:.0f}s)", flush=True)
    print(f"  enrichis: {len(enriched)} en {time.time() - t0:.0f}s", flush=True)

    for it in enriched:
        row = upsert_listing(db, it)
        row.set_ids = [SET_ID]
    db.commit()
    print(f"\nEn base : {db.query(Listing).count()} biens.", flush=True)

    if args.no_export:
        print("\nExport sauté (--no-export) : les biens ne sont QUE dans la base.", flush=True)
    else:
        _exporter(db, "Export", pepites, meilleur_zone)
    db.close()
    print("TERMINÉ.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
