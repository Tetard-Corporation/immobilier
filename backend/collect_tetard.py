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

SOUS_SET_ID = 2
SOUS_SET_NAME = "Léo"
SOUS_SET_DESC = "Préférences perso de Léo : isolement assumé, grand terrain, vue panoramique."

PRIX_MAX = 250_000

# Pondérations 1-5. Deux critères mènent le classement : ce que le bien vaut pour son prix,
# et ce qu'on a devant la porte.
PREFERENCES = [
    {"kind": "rapport_qualite_prix", "weight": 5, "label": "Rapport qualité/prix (vs prix du secteur)",
     "params": {"bon": 0.75, "cher": 1.7}},
    {"kind": "budget", "weight": 4, "label": "Budget ≤ 250 000 €", "params": {"budget_max": PRIX_MAX}},
    {"kind": "chambres_min", "weight": 4, "label": "3 chambres minimum", "params": {"min": 3}},
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
    {"kind": "logement_compact", "weight": 4, "label": "Format maison de retrait (3 à 5 chambres, pas immense)",
     "params": {"ideal": 4, "max": 5, "m2_ok": 170, "m2_max": 300}},
    # « Un peu de travaux possible, mais pas une rénovation complète » : le critère garde
    # sa forme (habitable 1,0 · à rafraîchir 1,0 · à rénover 0,85 · gros travaux 0,4 ·
    # ruine 0,1), c'est le PALIER qui s'ouvre d'un cran, à 0,85.
    {"kind": "light_works", "weight": 4, "label": "Peu de travaux (rafraîchir oui, rénovation complète non)", "params": {}},
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
    {"kind": "jardin", "weight": 4, "label": "Jardin (requis)", "params": {"min_surface": 300}},
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
    {"kind": "hiking", "weight": 3, "label": "Randonnées au départ", "params": {}},
    # 4h30 et non 4h : le groupe a demandé le Beaufortain, mesuré à 4h10 porte-à-porte
    # (Val d'Arly 4h06 ; Albertville, elle, passe à 3h57). Laisser le plafond à 4h aurait
    # donné 0 sur ce critère à la moitié de la zone qu'on vient d'ajouter — collecter une
    # zone puis la noter zéro n'a pas de sens. Le barème reste décroissant et continue de
    # préférer le proche : 0,60 à 3h, 0,22 à 3h57, 0,13 à 4h10.
    {"kind": "temps_acces", "weight": 3, "label": "≤ 4h30 porte-à-porte depuis Paris",
     "params": {"max_minutes": 270}},
    # Isolement neutralisé : le groupe veut le calme, pas le bout du monde.
    {"kind": "tranquillite", "weight": 3, "label": "Calme, sans vis-à-vis, hors lotissement",
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
    {"kind": "ski", "weight": 2, "label": "Station de ski à proximité", "params": {"max_km": 30}},
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
EXIGENCES = [
    {
        # « Grand max 250 k€ » est un PLAFOND, pas une préférence. Le critère budget, lui,
        # est pondéré : il pénalise le dépassement sans l'exclure, et un bien excellent
        # partout ailleurs le compense sans peine. Mesuré : à 450 k€ de plafond, sept des
        # treize pépites étaient au-dessus de 300 k€, jusqu'à 417 k€ ; au plafond de
        # 300 k€, six des quatorze pépites publiées dépassaient encore 250 k€.
        # Le palier est bas (70) pour que le hors-budget sorte franchement du panier.
        "above": 70,
        "label": "Dans le budget (requis au-dessus de 70)",
        "requires": ["budget"],
        "mode": "all",
        "min_subscore": 0.79,  # = prix ≤ budget (la note au plafond exact vaut 0,80)
    },
    {
        # « Pas de gros travaux » est un PLANCHER, au même titre que le budget. Le critère
        # `light_works` étant pondéré, une ruine bien placée et bon marché se rattrapait
        # ailleurs.
        #
        # Le seuil s'ouvre d'un cran, de 0,95 à 0,85 : « un peu de travaux possible, mais
        # pas une rénovation complète ». Passent donc habitable (1,0), à rafraîchir (1,0)
        # et à rénover (0,85) ; restent écartés les gros travaux (0,4) et la ruine (0,1).
        # Le tour précédent avait rangé « à rénover » du côté des gros travaux ; le groupe
        # est revenu dessus. La distinction tient : `classify` réserve gros_travaux aux
        # formules « rénovation complète / totale / lourde », « gros œuvre », « tout à
        # refaire », « travaux importants » — c'est-à-dire exactement la rénovation
        # complète que le groupe refuse, et non le chantier de second œuvre qu'il accepte.
        #
        # L'état non renseigné ne valide pas — c'est 45 % des annonces, donc le palier
        # coûte cher. C'est assumé : sur un site fait pour voter, un bien dont on ignore
        # l'état ne se juge pas, exactement comme un bien sans photo.
        "above": 70,
        "label": "Habitable, à rafraîchir ou à rénover (requis au-dessus de 70)",
        "requires": ["light_works"],
        "mode": "all",
        "min_subscore": 0.85,
    },
    {
        # « Jardin requis ». Sans palier, un bien sans extérieur restait éligible : le
        # critère terrain était pondéré, donc rattrapable. Seuil 0,5 = environ 110 m²
        # mesurés, ou un extérieur décrit dans l'annonce sans surface (0,7). Ne rien
        # prouver du tout vaut `n/a`, et `n/a` ne remplit pas une exigence.
        "above": 70,
        "label": "Jardin (requis au-dessus de 70)",
        "requires": ["jardin"],
        "mode": "all",
        "min_subscore": 0.5,
    },
    {
        "above": 75,
        "label": "Capacité d'accueil prouvée (requis au-dessus de 75)",
        "requires": ["chambres_min"],
        "mode": "all",
        "min_subscore": 0.99,  # = le minimum de chambres atteint, estimation comprise
    },
    {
        # Le plafond de capacité, pendant du palier précédent. Il ne ferme la porte qu'aux
        # maisons vraiment immenses : 0,7 sur le barème de `logement_compact` laisse
        # passer jusqu'à 5 chambres (0,75) et arrête à 6 (0,375). Quand les chambres
        # manquent, le barème bascule sur la surface habitable et 0,7 vaut ~210 m².
        #
        # Le palier est haut (85) et non 78 : « 5 chambres ça reste ok » veut dire qu'un
        # grand bien excellent partout ailleurs a le droit de bien figurer. Ce n'est
        # qu'en tête de classement — là où on désigne LA maison de retrait — que le
        # format cesse d'être négociable.
        "above": 85,
        "label": "Format maison de retrait (requis au-dessus de 85)",
        "requires": ["logement_compact"],
        "mode": "all",
        "min_subscore": 0.7,
    },
    {
        # Une pépite est une bonne affaire PROUVÉE. Sans surface bâtie il n'y a pas de prix
        # au m², donc rien à comparer au secteur — et le bien monte précisément parce qu'il
        # est peu mesuré. Le palier ferme cette porte.
        "above": 78,
        "label": "Rapport qualité/prix mesuré (requis au-dessus de 78)",
        "requires": ["rapport_qualite_prix"],
        "mode": "all",
        "min_subscore": 0.5,
    },
    {
        "above": 85,
        "label": "Nature ou montagne avérée (requis au-dessus de 85)",
        "requires": ["coin_nature", "relief_mountain", "hiking"],
        "mode": "any",
        "min_subscore": 0.6,
    },
]

# Pivots de collecte : les parties MONTAGNE des départements déjà couverts par le set
# (26, 07, 73, 01, 43, 42). La zone ne change pas ; ce sont les points de départ qui
# quittent la vallée du Rhône, d'où venait la majorité du haut de classement précédent
# (Châteauneuf-sur-Isère, 154 m).
# Le porte-à-porte depuis Paris est mesuré, pas supposé (Vercors 3h04, Trièves 3h21,
# Chartreuse 3h29, Oisans 3h39, Aravis 3h53, Gap 3h54, Maurienne 3h59). Le Beaufortain,
# à 4h10, était écarté à ce titre : le groupe l'a redemandé, il entre, et le critère de
# temps d'accès passe à 4h30 pour ne pas le noter zéro d'office.
# Sous-set « Léo » : seules les DIFFÉRENCES avec le parent (fusion par `kind`).
PREFERENCES_LEO = [
    {"kind": "relief_mountain", "weight": 5, "label": "Montagne / relief", "params": {"ref_altitude": 800}},
    {"kind": "has_terrain", "weight": 4, "label": "Grand terrain (≥ 1 500 m²)", "params": {"min_surface": 1500}},
    # Léo, lui, veut l'isolement : poids par défaut du critère.
    {"kind": "tranquillite", "weight": 4, "label": "Isolé, au calme, sans vis-à-vis", "params": {}},
    {"kind": "feature", "weight": 3, "label": "Vue panoramique", "params": {"name": "vue_panoramique"}},
]


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
    # Préalpes drômoises (à l'est du Rhône)
    ("Diois / Die", 44.754, 5.370, ["26"]),
    ("Vercors drômois / La Chapelle-en-Vercors", 44.968, 5.415, ["26"]),
    # Alpes
    ("Chartreuse / Saint-Pierre", 45.335, 5.820, ["38", "73"]),
    ("Vercors isérois / Villard-de-Lans", 45.070, 5.553, ["38"]),
    ("Trièves / Mens", 44.815, 5.750, ["38"]),
    ("Matheysine / La Mure", 44.900, 5.787, ["38"]),
    ("Oisans / Bourg-d'Oisans", 45.055, 6.030, ["38"]),
    ("Grésivaudan-Belledonne / Allevard", 45.395, 6.075, ["38", "73"]),
    ("Bauges / Le Châtelard", 45.700, 6.110, ["73"]),
    ("Maurienne / Saint-Jean", 45.276, 6.352, ["73"]),
    ("Aravis-Bornes / Thônes", 45.881, 6.325, ["74"]),
    ("Dévoluy / Gap", 44.620, 5.995, ["05"]),
    ("Bugey / Hauteville-Lompnes", 45.980, 5.600, ["01"]),
    # Demandés par le groupe (30 août) : « la zone autour d'Albertville, dans le
    # Beaufortain par exemple ». Trois pivots plutôt qu'un, parce que les rayons de
    # collecte (8/16/25 km) autour d'Albertville seul s'arrêteraient au seuil du
    # Beaufortain — la vallée du Doron est à 20 km de la ville, le Val d'Arly à 25.
    ("Albertville / Combe de Savoie", 45.676, 6.393, ["73"]),
    ("Beaufortain / Beaufort", 45.721, 6.575, ["73"]),
    ("Val d'Arly / Flumet", 45.816, 6.517, ["73", "74"]),
]


def ensure_sets(db) -> None:
    criteria = {"property_types": ["maison"], "preferences": PREFERENCES,
                "exigences": EXIGENCES,
                # La zone appartient au set : elle se réapplique à chaque export sans
                # qu'il faille repasser sur la base, et un bien collecté à l'ouest par
                # une future recherche est écarté tout seul.
                "zone": {"est_axe_lyon_valence": True}}
    fs = db.get(FilterSet, SET_ID)
    if fs is None:
        db.add(FilterSet(id=SET_ID, name=SET_NAME, description=SET_DESC, criteria=criteria))
    else:
        fs.name, fs.description, fs.criteria = SET_NAME, SET_DESC, criteria
    # Le sous-set ne porte que ses différences : le reste (dont la zone et les paliers)
    # est hérité par `resolve_criteria`.
    sous = db.get(FilterSet, SOUS_SET_ID)
    criteria_leo = {"preferences": PREFERENCES_LEO}
    if sous is None:
        db.add(FilterSet(id=SOUS_SET_ID, name=SOUS_SET_NAME, description=SOUS_SET_DESC,
                         criteria=criteria_leo, parent_id=SET_ID))
    else:
        sous.name, sous.description, sous.criteria, sous.parent_id = (
            SOUS_SET_NAME, SOUS_SET_DESC, criteria_leo, SET_ID)
    db.commit()
    print(f"Sets prêts : « {SET_NAME} » ({len(PREFERENCES)} critères, {len(EXIGENCES)} paliers, "
          f"≤{PRIX_MAX // 1000}k, est du Rhône) et « {SOUS_SET_NAME} » "
          f"({len(PREFERENCES_LEO)} surcharges).", flush=True)


def _seuils(texte: str | None) -> dict:
    """« 1:78.5,4:80 » -> {1: 78.5, 4: 80.0}."""
    out = {}
    for morceau in (texte or "").split(","):
        if ":" in morceau:
            sid, seuil = morceau.split(":", 1)
            out[int(sid.strip())] = float(seuil.strip())
    return out


def _exporter(db, quoi: str, pepites: dict | None = None) -> None:
    from app.services.export_static import export_to_dir

    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    print(f"\n{quoi} vers {os.path.abspath(data_dir)}...", flush=True)
    if pepites:
        print(f"  resserrage : {', '.join(f'set {k} ≥ {v:g}' for k, v in pepites.items())}", flush=True)
    t = time.time()
    stats = export_to_dir(db, data_dir, download_photos=True, pepites=pepites or None)
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

    if args.rescore_only:
        _exporter(db, "Re-score seul : ré-export", pepites)
        db.close()
        print("TERMINÉ.", flush=True)
        return 0

    from dataclasses import asdict

    from app.sources.base import NormalizedListing

    existing = {e for (e,) in db.query(Listing.external_id).filter(Listing.source == "bienici").all()}
    collected: dict[str, object] = {}

    if args.from_dump:
        with open(args.from_dump, encoding="utf-8") as fh:
            brut = json.load(fh)
        for d in brut:
            it = NormalizedListing(**d)
            if it.external_id and it.external_id not in existing:
                collected[it.external_id] = it
        print(f"Dump relu : {len(collected)} annonces neuves sur {len(brut)} "
              f"({os.path.abspath(args.from_dump)})", flush=True)
        return _traiter(db, args, collected, pepites)

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
            if not it.external_id or it.external_id in existing or it.external_id in collected:
                continue
            if it.prix is not None and it.prix > PRIX_MAX:
                continue
            collected[it.external_id] = it
            neufs += 1
        print(f"  [{nom}] {neufs} neufs (sur {len(items)} annonces)", flush=True)

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as fh:
            json.dump([asdict(it) for it in collected.values()], fh, ensure_ascii=False)
        print(f"\nDump écrit : {len(collected)} annonces -> {os.path.abspath(args.dump)}", flush=True)

    if args.collect_only:
        db.close()
        print("Collecte seule (--collect-only) : rien d'enrichi, rien d'exporté.", flush=True)
        return 0

    return _traiter(db, args, collected, pepites)


def _traiter(db, args, collected: dict, pepites: dict) -> int:
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
        _exporter(db, "Export", pepites)
    db.close()
    print("TERMINÉ.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
