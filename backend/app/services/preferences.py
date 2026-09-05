"""Moteur de préférences pondérées (régime ranking).

Aucune préférence n'exclut un bien : chacune produit un sous-score [0,1] (ou None si
non applicable / donnée manquante), agrégé en `match_score` [0,100] qui sert à classer.
Les préférences dépendant d'un enrichissement non encore branché (trajet train, fibre,
relief, rando) renvoient un statut `pending` tant que la donnée n'est pas disponible.
"""

from __future__ import annotations

import math

from .gares import nearest_gare
from .geo import distance_to_corridor_km, haversine_km, resolve_city
# Risques et qualité de l'eau sont DÉJÀ mesurés, pour le score d'investissement. On
# importe la mesure au lieu d'en écrire une seconde : deux barèmes pour un même aléa
# finiraient par diverger, et c'est le genre d'écart que personne ne va voir.
from .scoring import _pollution_eau as _mesure_eau
from .scoring import _risques_naturels as _mesure_risques

# Préférences évaluables dès maintenant (annonce + géo) ; le reste = pending.
PREFERENCE_KINDS = [
    "budget",
    "chambres_min",
    "has_terrain",
    "jardin",
    "ensoleillement",
    "constructible",
    "prix_m2_terrain",
    "rapport_qualite_prix",
    "en_hauteur_geo",
    "distance_mer",
    "surface_habitable",
    "light_works",
    "no_vis_a_vis",
    "tranquillite",
    "coin_nature",
    "logement_compact",
    "espace_modulable",
    "nature_exception",
    "authentic",
    "pas_pavillon",
    "feature",
    "near_corridor",
    "near_gare",
    "near_city",
    "temps_acces",
    "nuisance_sonore",
    "commerces",
    "village_vivant",
    "cachet",
    "dpe",
    "risques_naturels",
    "qualite_eau",
    "tension_locative",
    "ski",
    "attractivite_airbnb",
    "population_jeune",
    "orientation_gauche",
    # Dépendent d'un provider d'enrichissement (Lot A) :
    "rail_time_from",
    "fiber",
    "relief_mountain",
    "hiking",
]

_PENDING_KINDS = {"rail_time_from", "fiber", "relief_mountain", "hiking"}
# Randonnée : la donnée est un NOMBRE de sentiers relevés autour du bien (1 à 300 sur la
# zone têtard, médiane 88), et le critère n'en faisait qu'un oui/non — donc 1,00 pour
# 94 % des biens, écart-type 0,02. Il ne départageait personne : il remontait seulement
# tous les scores, et il satisfaisait à lui seul le palier « nature ou montagne avérée »,
# qui ne plafonnait donc jamais rien. En montagne, ce qui distingue deux villages n'est
# pas d'avoir des sentiers, c'est leur densité. Repères = les déciles mesurés.
_RANDO_PEU = 10
_RANDO_BEAUCOUP = 200
# DPE : la classe énergie, renseignée par 81 % des annonces du set têtard. Le barème n'est
# pas linéaire, parce que le coût ne l'est pas : F et G sont des passoires — interdites à
# la location (G depuis 2025, F en 2028) et donc porteuses d'un chantier que le prix
# affiché ne contient pas. A à C se valent presque ; la marche est entre E et F.
_DPE_ECHELLE = {"A": 1.0, "B": 0.95, "C": 0.85, "D": 0.70, "E": 0.50, "F": 0.25, "G": 0.10}
# Budget : part du budget en dessous de laquelle on est pleinement dans la « bonne
# affaire » (1.0), et note obtenue en consommant exactement 100 % du budget.
_BUDGET_CONFORT = 0.70
_BUDGET_LIMITE = 0.80
# Dépassement à partir duquel la note budget vaut zéro.
_BUDGET_DEPASSEMENT_NUL = 0.15
# Prix PLANCHER : en dessous, la note redescend. « Pas de secret : quand un bien est à
# 100 ou 150 k€, c'est qu'il y a un problème » — un problème que l'annonce ne nomme
# jamais (emplacement, mitoyenneté, humidité, second œuvre entier, bail). Sans ce
# plancher, le critère budget récompensait le bon marché : à 250 k€ de plafond, tout ce
# qui était sous 175 k€ prenait 1,0, et un mobil-home à 75 000 € marquait autant qu'une
# maison à 175 000 €.
# Le plancher est un SEUIL, pas une pente : franchir 180 000 € par le bas fait tomber la
# note d'un cran net (0,78 juste en dessous contre 0,99 juste au-dessus), puis elle
# décroît jusqu'à 0,15 à mi-plancher. Sans ce cran, un bien à 179 000 € notait encore
# 1,0 et le palier de prix ne mordait qu'à partir de 158 000 € — pas là où il est écrit.
# Minimum non nul (0,15) : c'est un a priori très fort, pas une preuve.
_BUDGET_SOUS_PLANCHER = 0.78
_BUDGET_PLANCHER_MIN = 0.15
# Note obtenue au seuil « cher » du €/m² de terrain ; au-delà la note continue de
# décroître en 1/prix (un terrain au prix parisien tombe vers 0).
_CHER_FLOOR = 0.25
# Tranquillité : note de départ quand l'annonce ne dit rien (ni vis-à-vis, ni isolement,
# ni lotissement). Neutre, pour que le critère puisse monter ET descendre.
_TRANQ_SOCLE = 0.50
# Logement compact : note à la limite haute acceptée (4 chambres) ; au-delà on halve
# par chambre supplémentaire.
_NOTE_LIMITE = 0.75
# « Coin de nature » : somme des poids au-delà de laquelle on a la note pleine. Calée
# pour que le cas idéal décrit — une rivière ET une vue dégagée — vaille 1,0 : on ne
# demande pas à une annonce de tout cocher.
_NATURE_SATURATION = 0.75
# Poids des signaux, la rivière/l'eau en tête (« le must »).
_NATURE_SIGNAUX = [
    ("eau", 0.45, "eau (rivière, ruisseau, étang)"),
    ("vue_panoramique", 0.30, "vue dégagée"),
    ("arbore", 0.25, "terrain arboré"),
    ("foret", 0.20, "bois / forêt"),
    ("vue", 0.15, "vue"),
]
# Ensoleillement : part de la note qui vient de la DURÉE mesurée, le reste venant de
# l'exposition du versant. La durée pèse plus parce qu'elle décide : un adret plein sud
# au fond d'une combe fermée ne voit pas le soleil de l'hiver, et son orientation n'y
# change rien.
_POIDS_DUREE = 0.7
# Pente à partir de laquelle l'orientation du versant compte pleinement. En dessous, le
# terrain est trop plat pour que « exposé sud » veuille dire quelque chose.
_PENTE_PLEINE = 20.0
# Volume de travaux. « à rénover » valait 0,85 — c'est-à-dire presque autant qu'une maison
# habitable — sur un critère qui s'appelle « peu de travaux, rénovation complète non ».
# La note contredisait son propre libellé, et comme le palier exigeait exactement 0,85,
# les 20 % de biens rangés en « à rénover » passaient tous, à la virgule près : la moindre
# erreur de classement traversait le filtre (une ruine notée 1★ par le groupe y est passée
# avec 0,85 sur ce critère).
#
# On sépare donc les deux rôles. L'ADMISSIBILITÉ ne bouge pas — le groupe a tranché le
# 30 août que « à rénover » reste acceptable, le palier descend à 0,6 pour le dire. La
# NOTE, elle, redevient honnête : une rénovation coûte, elle ne vaut pas « habitable ».
_LIGHT_OK = {"habitable": 1.0, "rafraichir": 1.0, "renover": 0.65, "gros_travaux": 0.4, "ruine": 0.1}
# Du plus léger au plus lourd : sert au seuil `min_etat` (« pas en dessous de ça »).
_NIVEAU_ETAT = {"habitable": 0, "rafraichir": 1, "renover": 2, "gros_travaux": 3, "ruine": 4}
# Ce qui reste d'une note quand le bien passe sous le seuil demandé (état, DPE).
_SOUS_SEUIL = 0.25
_COND_LABELS = {
    "habitable": "habitable de suite", "rafraichir": "à rafraîchir", "renover": "à rénover",
    "gros_travaux": "gros travaux", "ruine": "ruine / à reconstruire",
}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


# Étirement du score agrégé sur toute l'échelle 0-100.
#
# La moyenne pondérée d'une douzaine de critères se concentre mécaniquement au centre :
# il faudrait qu'un bien soit excellent PARTOUT pour dépasser 85, et mauvais partout pour
# descendre sous 30. Résultat mesuré avant correction : 90 % des biens entre 50 et 79,
# maximum 88 — les pépites ne se détachaient pas du lot.
#
# On applique donc une transformation AFFINE entre deux ancres fixes : la moyenne
# pondérée réellement atteignable en bas (0,20 = un bien qui ne coche presque rien) et en
# haut (0,90 = un bien qui coche presque tout). Fixes = le score reste absolu : il ne
# dépend que du bien et du set, pas des autres annonces du lot. Transformation monotone :
# le classement est rigoureusement identique, seul l'étalement change.
# Ancres PAR DÉFAUT. Chaque set déclare les siennes (`criteria["ancres"]`), parce que les
# trois sets ne cherchent pas la même chose et n'atteignent pas les mêmes moyennes :
# mesuré le 5 septembre 2026, têtard va de 0,42 à 0,79 de moyenne pondérée, Pauline de
# 0,40 à 0,86, le littoral de 0,48 à 0,83. Avec une paire commune, chacun n'utilisait
# qu'à peine la moitié de l'échelle (têtard 31→84, littoral 39→90) et les scores de deux
# sets se comparaient sans que rien ne le justifie — ce sont des groupes différents, qui
# ne se prêtent pas de biens.
_ANCRE_BASSE = 0.20
_ANCRE_HAUTE = 0.90


def _contraste(x: float, basse: float = _ANCRE_BASSE, haute: float = _ANCRE_HAUTE) -> float:
    if haute <= basse:
        return _clamp(x)
    return _clamp((x - basse) / (haute - basse))


def ancres_de(criteria: dict | None) -> tuple[float, float]:
    """Les ancres déclarées par un set, ou celles par défaut."""
    a = (criteria or {}).get("ancres") or {}
    try:
        basse = float(a.get("basse", _ANCRE_BASSE))
        haute = float(a.get("haute", _ANCRE_HAUTE))
    except (TypeError, ValueError):
        return _ANCRE_BASSE, _ANCRE_HAUTE
    return (basse, haute) if haute > basse else (_ANCRE_BASSE, _ANCRE_HAUTE)


# Tracés ferroviaires réels (hubs intermédiaires) pour les axes courants : un axe
# Paris-Marseille ne suit pas la ligne droite (qui coupe le Massif Central) mais la
# vallée du Rhône. On insère les hubs quand l'axe correspond à un trajet connu.
_KNOWN_RAIL_HUBS = {
    frozenset({"paris", "marseille"}): ["paris", "dijon", "lyon", "valence", "avignon", "marseille"],
    frozenset({"paris", "lyon"}): ["paris", "dijon", "lyon"],
    frozenset({"paris", "nice"}): ["paris", "lyon", "valence", "avignon", "marseille", "nice"],
    frozenset({"paris", "montpellier"}): ["paris", "lyon", "valence", "nimes", "montpellier"],
}


def _corridor_points(params: dict) -> list[tuple[float, float]]:
    pts = [tuple(p) for p in params.get("points", []) if isinstance(p, (list, tuple)) and len(p) == 2]
    villes = [v for v in (params.get("villes", []) or params.get("cities", []))]
    # Si l'axe relie deux villes correspondant à un trajet ferroviaire connu, on suit
    # le tracé réel (hubs intermédiaires) plutôt que la ligne droite.
    if len(villes) == 2:
        key = frozenset(v.strip().lower() for v in villes)
        if key in _KNOWN_RAIL_HUBS:
            villes = _KNOWN_RAIL_HUBS[key]
    for city in villes:
        c = resolve_city(city)
        if c:
            pts.append(c)
    return pts


def _eval_one(item, kind: str, params: dict):
    """Renvoie (subscore|None, status, detail_str)."""
    flags = item.flags or {}

    if kind == "budget":
        if item.prix is None:
            return None, "n/a", "prix inconnu"
        budget = params.get("budget_max") or (params.get("apport", 0) * params.get("levier", 4))
        if not budget:
            return None, "n/a", "budget non défini"
        # Plancher : en dessous, un prix n'est plus une bonne affaire mais un signal.
        plancher = params.get("budget_min")
        if plancher and item.prix < plancher:
            bas = plancher / 2.0
            part = _clamp((item.prix - bas) / (plancher - bas))
            sub = _BUDGET_PLANCHER_MIN + (_BUDGET_SOUS_PLANCHER - _BUDGET_PLANCHER_MIN) * part
            return sub, "ok", (f"{int(item.prix)}€ — sous le plancher de {int(plancher)}€ "
                               f"(un prix de ce niveau cache en général un défaut)")
        ratio = item.prix / budget
        if ratio <= _BUDGET_CONFORT:
            # On cherche la bonne affaire, pas le bien qui « rentre tout juste » : le
            # plein score est réservé à ce qui laisse de la marge (travaux, négociation).
            return 1.0, "ok", f"{int(item.prix)}€ — {round(ratio * 100)}% du budget {int(budget)}€"
        if ratio <= 1.0:
            sub = 1.0 - (ratio - _BUDGET_CONFORT) / (1.0 - _BUDGET_CONFORT) * (1.0 - _BUDGET_LIMITE)
            return sub, "ok", f"{int(item.prix)}€ — {round(ratio * 100)}% du budget {int(budget)}€ (haut de fourchette)"
        # Hors budget = quasi rédhibitoire (retour récurrent du groupe). C'était un PALIER
        # qui le disait — plafond à 70 tant que le budget n'était pas tenu. Les paliers
        # ayant été retirés (ils aplatissaient le classement de tout le monde), la note
        # doit le porter seule : elle tombe à zéro dès +15 % au lieu de +33 %.
        # +5 % -> 0,53 · +10 % -> 0,27 · +15 % et au-delà -> 0.
        over = ratio - 1.0
        return _BUDGET_LIMITE * _clamp(1 - over / _BUDGET_DEPASSEMENT_NUL), "ok", \
            f"{int(item.prix)}€ > budget {int(budget)}€ (+{round(over * 100)}%, hors budget)"

    if kind == "chambres_min":
        mn = params.get("min", 1)
        nb, source = item.nb_chambres, "ch."
        if nb is None:
            # Sans repli, le critère est `n/a` — donc NEUTRE — sur la moitié des annonces,
            # et une maison d'une seule pièce peut finir deuxième du classement d'un set
            # qui demande quatre chambres. C'est arrivé (Montcel, 87,8). Les pièces sont
            # données par 91 % des annonces contre 51 % pour les chambres.
            pieces = getattr(item, "nb_pieces", None)
            if pieces:
                nb, source = max(1, int(pieces) - 1), "ch. estimées (pièces - 1)"
                # Recoupement par la surface : une annonce peut annoncer 4 pièces dans
                # 35 m². Vécu — un mobil-home de camping à 75 000 € est ainsi entré dans
                # les pépites avec « 3 chambres estimées », c'est-à-dire en franchissant
                # le palier de capacité d'accueil sans avoir la surface de les loger.
                # 20 m² par pièce, part des communs comprise : large, mais assez pour
                # rendre impossible ce que l'annonce prétendait.
                surface = getattr(item, "surface_bati", None)
                plafond_m2 = params.get("m2_min_par_piece", 20)
                if surface and plafond_m2:
                    tenable = max(1, int(surface // plafond_m2) - 1)
                    if tenable < nb:
                        nb = tenable
                        source = f"ch. tenables dans {int(surface)} m² (l'annonce en annonce plus)"
        if nb is None:
            # Dernier repli : la surface habitable, à raison d'une chambre par tranche.
            s = getattr(item, "surface_bati", None)
            m2 = params.get("m2_par_chambre", 35)
            if s:
                nb, source = max(1, int(s // m2)), f"ch. estimées ({m2} m²/ch.)"
        if nb is None:
            return None, "n/a", "nb chambres inconnu"
        if nb >= mn:
            sub = 1.0
        else:
            # En dessous du minimum -> dégradé linéaire (et non un mur à 0) :
            # 3/4 = 0.75, 2/4 = 0.5, 1/4 = 0.25. Garde la direction (plus de chambres = mieux)
            # sans écraser le match des biens à rénover (souvent 2-3 ch).
            sub = _clamp(nb / mn)
        return sub, "ok", f"{nb} {source} (min {mn})"

    if kind == "has_terrain":
        if item.surface_terrain is None:
            return None, "n/a", "surface terrain inconnue"
        mn = params.get("min_surface", 1)
        seuil = f" (souhait ≥ {int(mn)} m²)" if mn and mn > 1 else ""
        return (1.0 if item.surface_terrain >= mn else _clamp(item.surface_terrain / mn)), "ok", f"{int(item.surface_terrain)} m²{seuil}"

    if kind == "jardin":
        # « Jardin requis » : un PLANCHER, pas un souhait de grand terrain (c'est le rôle
        # de `has_terrain`). D'où deux différences avec lui : le seuil est bas, et le
        # silence de l'annonce ne vaut pas absence de jardin — 14 % des maisons du set ne
        # donnent aucune surface de terrain, souvent avec « jardin » en toutes lettres
        # dans le texte. Les compter comme sans extérieur les écarterait à tort ; leur
        # donner la note pleine ferait passer un plancher pour une formalité, donc on les
        # note au-dessous de la mesure.
        mn = params.get("min_surface", 300)
        st = item.surface_terrain
        if st:
            if st >= mn:
                return 1.0, "ok", f"jardin de {int(st)} m² (requis ≥ {int(mn)} m²)"
            return _clamp(0.35 + 0.65 * st / mn), "ok", f"{int(st)} m² d'extérieur (petit, requis ≥ {int(mn)} m²)"
        if "jardin" in (flags.get("features") or []):
            return params.get("note_mention", 0.7), "ok", "jardin mentionné (surface non précisée)"
        # Ni surface ni mention : `n/a` -> exclu du score ET du palier « jardin requis »,
        # qui plafonne alors le bien. Ne pas prouver l'extérieur, c'est ne pas l'avoir.
        return None, "n/a", "aucun extérieur mentionné, surface terrain inconnue"

    if kind == "constructible":
        # Terrain à bâtir (ex. poser une tiny house) : s'appuie sur le zonage PLU (GPU).
        zone = flags.get("zone_urba")
        if flags.get("constructible"):
            return 1.0, "ok", f"constructible (zonage {zone or 'U'})"
        if flags.get("est_zone_au"):
            return 0.6, "ok", f"zone AU — bientôt constructible ({zone or 'AU'})"
        if flags.get("constructible") is False:
            return 0.1, "ok", f"non constructible (zonage {zone or 'A/N'})"
        return None, "n/a", "constructibilité inconnue (zonage non résolu)"

    if kind == "prix_m2_terrain":
        # Rapport qualité/prix pour un TERRAIN : €/m² de terrain (fiable, contrairement à
        # l'écart DVF qui compare au bâti). Barème pour du rural/côtier breton.
        st = item.surface_terrain
        if item.prix is None or not st:
            return None, "n/a", "prix ou surface terrain inconnu"
        ppm = item.prix / st
        bon = params.get("bon", 80)     # €/m² : excellent (terrain nature/agricole viabilisable)
        cher = params.get("cher", 400)  # €/m² : cher (lotissement viabilisé prisé du littoral)
        if ppm <= bon:
            sub = 1.0
        elif ppm >= cher:
            # Au-delà de « cher », on continue de décroître au lieu de plafonner à un
            # plancher : sinon un terrain à 400 €/m² et un autre à 1000 €/m² (prix
            # parisien) obtiennent la même note et le critère ne trie plus rien.
            sub = _CHER_FLOOR * cher / ppm
        else:
            sub = _clamp(1 - (ppm - bon) / (cher - bon) * (1 - _CHER_FLOOR))
        return sub, "ok", f"{round(ppm)} €/m² de terrain"

    if kind == "rapport_qualite_prix":
        # Bonne affaire sur du BÂTI : prix au m² du bien rapporté au prix au m² observé
        # dans le secteur (DVF, transactions réelles). C'est le RATIO qui parle, pas le
        # prix absolu — 2 000 €/m² est cher en Ardèche et donné en Savoie du lac.
        # Renseigné sur 87 % des biens du set têtard.
        pm2 = flags.get("prix_m2_secteur")
        s = getattr(item, "surface_bati", None)
        if item.prix is None or not s or not pm2:
            return None, "n/a", "prix, surface bâtie ou référence de secteur manquante"
        ratio = (item.prix / s) / pm2
        # Repères mesurés sur les 393 biens notés du set : p20 = 0,75 · médiane = 1,17 ·
        # p80 = 1,68. Le prix affiché est structurellement au-dessus des ventes passées ;
        # les ancres sont donc calées sur la distribution réelle, pas sur la parité.
        bon = params.get("bon", 0.75)
        cher = params.get("cher", 1.7)
        if ratio <= bon:
            sub = 1.0
        elif ratio >= cher:
            # Au-delà de « cher », on continue de décroître en 1/ratio : sinon un bien à
            # deux fois le prix du secteur et un autre à quatre fois se valent.
            sub = _CHER_FLOOR * cher / ratio
        else:
            sub = _clamp(1 - (ratio - bon) / (cher - bon) * (1 - _CHER_FLOOR))
        ecart = round((ratio - 1) * 100)
        situe = f"+{ecart} %" if ecart > 0 else f"{ecart} %"
        return sub, "ok", f"{round(item.prix / s)} €/m² — {situe} du secteur ({round(pm2)} €/m²)"

    if kind == "en_hauteur_geo":
        # Proéminence locale (m) = altitude du point − alentours (couronne 300 m).
        # Mesure RÉELLE du « surélevé/dominant » (ex. Ti Louzou ≈ +6 m), là où
        # l'altitude absolue ne dit rien (la côte est basse).
        p = flags.get("prominence_m")
        if p is None:
            return None, "n/a", "relief non calculé"
        # +8 m et plus = très dominant ; 0 = plat ; négatif = en creux.
        sub = _clamp(0.3 + p / 12.0)
        pos = "dominant" if p >= 4 else ("plat" if p > -2 else "en creux")
        return sub, "ok", f"{pos} ({p:+.0f} m sur 300 m)"

    if kind == "distance_mer":
        # Distance réelle à la côte (m), mesurée via l'IGN (cf. export). Barème :
        # pieds dans l'eau -> loin. Paramétrable (proche/loin en m).
        dm = flags.get("dist_mer_m")
        if dm is None:
            return None, "n/a", "distance mer non calculée"
        proche = params.get("proche", 300)   # ≤ -> excellent
        loin = params.get("loin", 3000)       # ≥ -> négligeable
        if dm <= proche:
            sub = 1.0
        elif dm >= loin:
            sub = 0.1
        else:
            sub = _clamp(1 - (dm - proche) / (loin - proche) * 0.9)
        return sub, "ok", f"mer à ~{int(dm)} m" if dm <= loin else f"mer à > {loin} m"

    if kind == "surface_habitable":
        s = getattr(item, "surface_bati", None)
        if s is None:
            return None, "n/a", "surface habitable inconnue"
        mn = params.get("min", 80)
        return (1.0 if s >= mn else _clamp(s / mn)), "ok", f"{int(s)} m² habitables (souhait ≥ {int(mn)} m²)"

    if kind == "light_works":
        cond = flags.get("condition")
        if cond is None:
            return None, "n/a", "état inconnu"
        note = _LIGHT_OK.get(cond, 0.6)
        # SEUIL, optionnel : « en dessous de cet état, pour moi c'est non ». Une moyenne
        # sur vingt-sept critères ne peut pas couler un bien excellent partout ailleurs —
        # une grange à aménager à 1 030 m avec 1,5 ha sortait 18e malgré sa note de 0,4.
        # Le palier du set faisait ce travail ; il a été retiré parce qu'il s'imposait à
        # tout le monde. Le seuil, lui, appartient à celui qui le pose : le groupe pour le
        # set, chacun pour sa lentille.
        seuil = params.get("min_etat")
        if seuil in _LIGHT_OK and _NIVEAU_ETAT.get(cond, 9) > _NIVEAU_ETAT.get(seuil, 9):
            # La note du BIEN s'effondre, pas celle du seuil : sinon un seuil plus strict
            # produirait une pénalité plus douce (« à rafraîchir » vaut 1,0, « à rénover »
            # 0,65 — le plancher aurait été plus haut avec le seuil le plus exigeant).
            note = note * _SOUS_SEUIL
            return note, "ok", f"état : {_COND_LABELS.get(cond, cond)} (sous ton seuil : {_COND_LABELS.get(seuil, seuil)})"
        return note, "ok", f"état : {_COND_LABELS.get(cond, cond)}"

    if kind == "dpe":
        classe = (getattr(item, "dpe_classe", None) or flags.get("dpe_classe") or "")
        classe = classe.strip().upper()[:1]
        if classe not in _DPE_ECHELLE:
            return None, "n/a", "DPE non renseigné"
        sub = _DPE_ECHELLE[classe]
        seuil = str(params.get("min_classe") or "").strip().upper()[:1]
        if seuil in _DPE_ECHELLE and classe > seuil:
            sub *= _SOUS_SEUIL
        quoi = ("passoire thermique" if classe in ("F", "G")
                else "performant" if classe in ("A", "B") else "correct")
        return sub, "ok", f"DPE {classe} — {quoi}"

    if kind == "risques_naturels":
        # Même mesure que le pilier « Risques » du score d'investissement (aléas
        # Géorisques pondérés par leur gravité pour un logement) — mais PONDÉRABLE ici :
        # le score d'investissement dit ce que le bien vaut, le set dit ce que le groupe
        # accepte de vivre. Les deux n'ont pas à donner le même poids à l'inondation.
        return _mesure_risques(flags, {})

    if kind == "qualite_eau":
        return _mesure_eau(flags, {})

    if kind == "no_vis_a_vis":
        if "sans_vis_a_vis" in (flags.get("features") or []):
            return 1.0, "ok", "sans vis-à-vis"
        if "vis_a_vis" in (flags.get("nuisances") or []):
            return 0.0, "ok", "vis-à-vis signalé"
        # Donnée absente : on n'invente pas une pénalité -> n/a (exclu du dénominateur).
        return None, "n/a", "vis-à-vis non précisé (ignoré)"

    if kind == "nature_exception":
        ns = flags.get("nature_score") or 0
        if flags.get("nature_exception"):
            return 1.0, "ok", "site d'exception (nature remarquable)"
        return _clamp(0.4 + 0.1 * ns), "ok", f"qualité nature {ns}/4"

    if kind == "authentic":
        present = "authentique" in (flags.get("features") or [])
        # Cachet non mentionné ≠ sans cachet (annonces incomplètes) -> n/a plutôt que pénalité.
        if present:
            return 1.0, "ok", "cachet / authentique mentionné"
        return None, "n/a", "cachet non mentionné (ignoré)"

    if kind == "pas_pavillon":
        # Critère négatif (retour du groupe : « pavillon, on ne veut pas du neuf »).
        pav = flags.get("pavillon_neuf")
        if pav is None:
            return 0.7, "ok", "style indéterminé"
        return (0.15 if pav else 1.0), "ok", "pavillon / neuf détecté" if pav else "pas de signe pavillon/neuf"

    if kind == "tranquillite":
        # « Un minimum de vis-à-vis, surtout pas du pavillonnaire/résidentiel, isolé c'est
        # mieux » : les trois expriment une même chose, on les note ensemble.
        # TOUJOURS évaluable, et à deux sens : pris séparément, ces signaux ne sont cités
        # que par 4 à 15 % des annonces, donc en `n/a` ils ne servaient que de bonus et
        # aucun bien ne pouvait mal noter. Ici le silence vaut le socle neutre, les
        # mentions favorables montent et le lotissement fait descendre.
        # « Isolé c'est mieux » n'est pas universel : le set têtard veut le calme SANS
        # l'isolement (« pas isolé »). Les deux poids sont donc réglables, et un poids nul
        # retire le signal du calcul au lieu de le compter pour rien.
        p_isole = params.get("poids_isolement", 0.25)
        p_densite = params.get("poids_densite", 0.20)
        feats, nuis = flags.get("features") or [], flags.get("nuisances") or []
        note, motifs = _TRANQ_SOCLE, []
        if "sans_vis_a_vis" in feats:
            note += 0.30; motifs.append("sans vis-à-vis")
        if "isole" in feats and p_isole:
            note += p_isole; motifs.append("isolé / pleine nature")
        if "calme" in feats:
            note += 0.10; motifs.append("calme")
        iso = flags.get("isolement_score")
        if iso and p_densite:
            note += p_densite * iso
            motifs.append(f"commune peu dense ({flags.get('population_commune')} hab.)")
        if "vis_a_vis" in nuis:
            note -= 0.45; motifs.append("vis-à-vis signalé")
        if flags.get("pavillon_neuf"):
            note -= 0.45; motifs.append("pavillon / lotissement")
        return _clamp(note), "ok", " · ".join(motifs) or "rien de signalé"

    if kind == "coin_nature":
        # « Un petit coin de nature quand même » : rivière en tête, puis vue dégagée,
        # grands arbres, bois, hauteur. Somme pondérée rapportée à un seuil de
        # saturation : deux bons signaux suffisent pour la note pleine — on ne demande
        # pas à une annonce de tout cocher.
        feats = flags.get("features") or []
        acquis, motifs = 0.0, []
        for nom, poids, libelle in _NATURE_SIGNAUX:
            if nom in feats:
                acquis += poids
                motifs.append(libelle)
        alt = flags.get("altitude")
        if alt is not None and alt >= params.get("alt_min", 40):
            # « Vue dégagée car en hauteur » : en Bretagne sud, 40 m domine déjà, 100 m
            # est un point haut (médiane du secteur : 33 m).
            part = _clamp((alt - params.get("alt_min", 40)) / (params.get("alt_ref", 100) - params.get("alt_min", 40)))
            acquis += 0.25 * part
            motifs.append(f"en hauteur ({round(alt)} m)")
        return _clamp(acquis / params.get("saturation", _NATURE_SATURATION)), "ok", " · ".join(motifs) or "aucun élément naturel cité"

    if kind == "ensoleillement":
        # Exposition et DURÉE de soleil, mesurées sur le relief (cf. services/soleil.py) :
        # heures de soleil direct au solstice d'hiver, orientation et pente du versant.
        # C'est le critère que l'annonce ne peut pas donner — « plein sud » y est un
        # argument de vente, et deux maisons du même village, l'une à l'adret, l'autre à
        # l'ubac, ont la même altitude et pas le même hiver.
        h = flags.get("soleil_hiver_h")
        if h is None:
            if "ensoleille" in (flags.get("features") or []):
                # Repli texte : l'annonce le revendique, personne ne l'a mesuré. Note
                # plafonnée sous la note d'un versant réellement mesuré comme bon.
                return params.get("note_annonce", 0.7), "ok", "« plein sud / très ensoleillé » (annonce, non mesuré)"
            return None, "pending", "ensoleillement non mesuré (relief non échantillonné)"

        from .soleil import formater_heures

        faibles = params.get("heures_faibles", 1.5)
        bonnes = params.get("heures_bonnes", 6.0)
        part_duree = _clamp((h - faibles) / (bonnes - faibles)) if bonnes > faibles else 0.0

        # Exposition : plein sud = 1, plein nord = 0, et l'écart se resserre vers le neutre
        # quand la pente s'aplatit — sur un replat, l'orientation ne veut plus rien dire.
        expo = flags.get("exposition_deg")
        pente = flags.get("pente_deg") or 0.0
        if expo is None:
            part_expo, mot_expo = 0.5, "terrain plat"
        else:
            sud = (1 + math.cos(math.radians(expo - 180))) / 2
            part_expo = 0.5 + (sud - 0.5) * _clamp(pente / _PENTE_PLEINE)
            mot_expo = f"versant {flags.get('exposition') or ''} à {pente:.0f}°".strip()

        sub = _clamp(_POIDS_DUREE * part_duree + (1 - _POIDS_DUREE) * part_expo)
        masque = flags.get("masque_sud_deg")
        detail = f"{formater_heures(h)} de soleil le 21 décembre · {mot_expo}"
        if masque is not None:
            detail += f" · horizon sud barré à {masque:.0f}°"
        return sub, "ok", detail

    if kind == "logement_compact":
        # « De la tiny house jusqu'à 3/4 chambres » : on pénalise le TROP grand, jamais
        # le petit. Un terrain nu n'a pas de logement -> critère non applicable.
        if getattr(item, "type_bien", None) == "terrain":
            return None, "n/a", "terrain nu (pas de logement)"
        ideal, limite = params.get("ideal", 3), params.get("max", 4)
        ch, source = item.nb_chambres, "chambres"
        pieces = getattr(item, "nb_pieces", None)
        if ch is None and pieces:
            # Seules 69 % des annonces donnent les chambres, 98 % donnent les pièces. Sur
            # les 456 annonces qui donnent les deux, l'écart médian est de 1 (pièces - 1
            # tombe juste dans 52 % des cas, contre 31 % pour pièces - 2).
            ch, source = max(1, pieces - 1), "chambres estimées (pièces - 1)"
        if ch is None:
            s = getattr(item, "surface_bati", None)
            if s is None:
                return None, "n/a", "taille du logement inconnue"
            petit, grand = params.get("m2_ok", 120), params.get("m2_max", 250)
            sub = 1.0 if s <= petit else _clamp(1 - (s - petit) / (grand - petit))
            return sub, "ok", f"{int(s)} m² habitables (compact ≤ {petit} m²)"
        if ch <= ideal:
            return 1.0, "ok", f"{ch} {source} (≤ {ideal}, format recherché)"
        if ch <= limite:
            # Entre l'idéal et la limite haute acceptée : décote légère.
            return _NOTE_LIMITE, "ok", f"{ch} {source} (limite haute acceptée)"
        # Au-delà : on halve à chaque chambre supplémentaire. Décroissance continue plutôt
        # qu'un mur à 0, sinon une maison de 5 chambres et une de 8 se valent.
        return _NOTE_LIMITE * 0.5 ** (ch - limite), "ok", f"{ch} {source} (trop grand, > {limite})"

    if kind == "espace_modulable":
        # Le contrepoids de `logement_compact`, et sa question complémentaire : celui-là
        # plafonne la maison qu'on habite, celui-ci compte le volume qu'on peut convertir
        # en couchages pour le week-end où tout le monde vient. Les deux ne se paient pas
        # deux fois — grange, combles et dépendance ne comptent pas dans la surface
        # habitable (corrélation mesurée avec `surface_bati` : 0,04).
        from .modulable import noter, resumer

        if "espace_modulable" not in flags:
            # Le détecteur n'est pas passé sur ce bien (chemin live sans annotation).
            return None, "pending", "volumes convertibles non analysés"
        signaux = flags["espace_modulable"]
        if signaux is None:
            # Annonce sans texte : rien à lire n'est pas la même chose que rien à trouver.
            # Le socle pénaliserait un bien pour un silence qui n'est pas le sien.
            return None, "n/a", "annonce sans description (ignoré)"
        return noter(signaux, params), "ok", resumer(signaux)

    if kind == "feature":
        name = params.get("name")
        present = name in (flags.get("features") or [])
        # Pour l'isolement, on renforce le signal texte avec la densité communale.
        if name == "isole":
            iso = flags.get("isolement_score")
            pop = flags.get("population_commune")
            if present and iso is not None:
                return _clamp(0.7 + 0.3 * iso), "ok", f"isolé (commune {pop} hab.)"
            if iso is not None:
                detail = f"commune de {pop} hab." if pop is not None else "densité connue"
                return _clamp(iso), "ok", detail
        # Feature non citée dans l'annonce ≠ absente -> n/a (exclu), pas une pénalité à 0.2.
        if present:
            return 1.0, "ok", f"{name} : mentionné"
        return None, "n/a", f"{name} : non mentionné (ignoré)"

    if kind == "near_corridor":
        if item.latitude is None or item.longitude is None:
            return None, "n/a", "géoloc manquante"
        pts = _corridor_points(params)
        dist = distance_to_corridor_km(item.latitude, item.longitude, pts)
        if dist is None:
            return None, "n/a", "corridor non défini"
        max_km = params.get("max_km", 40)
        villes = params.get("villes") or params.get("cities") or []
        axe = " – ".join(v.capitalize() for v in villes) if villes else None
        return _clamp(1 - dist / max_km), "ok", f"{round(dist)} km de l'axe" + (f" {axe}" if axe else "")

    if kind == "near_gare":
        if item.latitude is None or item.longitude is None:
            return None, "n/a", "géoloc manquante"
        res = nearest_gare(item.latitude, item.longitude)
        if res is None:
            return None, "n/a", "données gares indispo"
        nom, dist = res
        max_km = params.get("max_km", 10)
        return _clamp(1 - dist / max_km), "ok", f"gare de {nom} à {dist} km"

    if kind == "near_city":
        if item.latitude is None or item.longitude is None:
            return None, "n/a", "géoloc manquante"
        center = resolve_city(params.get("ville")) or (params.get("lat"), params.get("lon"))
        if not center or center[0] is None:
            return None, "n/a", "ville non résolue"
        dist = haversine_km(item.latitude, item.longitude, center[0], center[1])
        ville = params.get("ville")
        suffixe = f" de {ville}" if ville else ""
        return _clamp(1 - dist / params.get("max_km", 50)), "ok", f"{round(dist)} km{suffixe}"

    if kind == "temps_acces":
        # Porte-à-porte depuis Paris (TGV vers le meilleur hub + voiture).
        from .geo import porte_a_porte_min

        if item.latitude is None or item.longitude is None:
            return None, "n/a", "géoloc manquante"
        minutes = porte_a_porte_min(item.latitude, item.longitude)
        if minutes is None:
            return None, "n/a", "trajet indéterminé"
        max_min = params.get("max_minutes", 240)
        h, m = divmod(minutes, 60)
        hmax = max_min // 60
        return _clamp(1 - (minutes - 120) / (max_min - 120)) if max_min > 120 else (1.0 if minutes <= max_min else 0.0), "ok", f"~{h}h{m:02d} porte-à-porte depuis Paris (max {hmax}h)"

    if kind == "nuisance_sonore":
        # Critère "calme" : pénalise la proximité d'une autoroute/voie ferrée (bruit).
        # subscore élevé = éloigné = calme. Données injectées à l'enrichissement/export.
        if not flags.get("infra_checked"):
            return None, "pending", "proximité infrastructures non vérifiée"
        da, dr = flags.get("dist_autoroute_m"), flags.get("dist_rail_m")
        # « Le long d'une route nationale », « le long d'une route » : deux biens notés
        # 1★ pour ça. Le critère ne regardait qu'autoroutes et voies ferrées, donc une
        # départementale passante devant la maison lui était invisible. Le bruit d'une
        # nationale porte moins loin que celui d'une autoroute : on la compte à distance
        # réduite (facteur `poids_route`) plutôt que de lui donner le même poids.
        dro = flags.get("dist_route_m")
        vals = [d for d in (da, dr) if d is not None]
        if dro is not None:
            vals.append(dro / params.get("poids_route", 0.45))
        if not vals:
            return 1.0, "ok", "aucune route passante, autoroute ni voie ferrée à proximité"
        min_m = params.get("min_m", 200)
        ref_m = params.get("ref_m", 1000)
        sub = _clamp((min(vals) - min_m) / (ref_m - min_m))
        parts = []
        if da is not None:
            parts.append(f"autoroute {da} m")
        if dr is not None:
            parts.append(f"voie ferrée {dr} m")
        if dro is not None:
            parts.append(f"route passante {dro} m")
        return sub, "ok", " · ".join(parts)

    if kind == "village_vivant":
        # « Trop en ville » (deux biens notés 1★ pour cette seule raison). Le critère
        # `commerces` sature : 15 commerces valent 1,0, et Chambéry avec 258 vaut 1,0
        # aussi. Il ne distinguait donc pas le village vivant de la ville — alors que
        # c'est précisément la distinction que le groupe fait.
        #
        # D'où une cloche : le désert commercial est mauvais, le bourg avec ses commerces
        # est l'optimum, et l'agglomération redescend. Un lieu de retrait entre copains
        # n'est ni un hameau sans boulangerie ni un centre-ville.
        n = flags.get("n_commerces")
        if n is None:
            return None, "pending", "commerces non vérifiés"
        vivant = params.get("vivant", 8)     # en dessous : trop isolé pour le quotidien
        ideal = params.get("ideal", 25)      # bourg pourvu : l'optimum
        ville = params.get("ville", 120)     # au-delà : on est en agglomération
        if n < vivant:
            sub = _clamp(0.25 + 0.75 * n / vivant)
            mot = f"{n} commerces/services (peu, {round(sub*100)}%)"
        elif n <= ideal:
            sub, mot = 1.0, f"{n} commerces/services (bourg vivant)"
        else:
            # Décroissance douce jusqu'au seuil « ville », puis plancher : au-delà, plus
            # de commerces ne rend pas le lieu plus urbain qu'il ne l'est déjà.
            sub = _clamp(1 - 0.7 * min(1.0, (n - ideal) / (ville - ideal)))
            mot = f"{n} commerces/services ({'ville' if n >= ville else 'gros bourg'})"
        return sub, "ok", mot

    if kind == "cachet":
        # « Pas de charme » revient trois fois en reproche (1★), « charme de la bâtisse »
        # une fois en éloge (4★) : le cachet compte. Il avait pourtant été retiré du set,
        # sur la consigne « pas le bâti ancien, ça veut dire travaux » — mais le motif
        # était les TRAVAUX, désormais couverts par leur propre palier. Le charme peut
        # donc revenir sans ramener le risque.
        #
        # Composite et toujours évaluable, comme `tranquillite` : « authentique » n'est
        # cité que par 38 % des annonces, donc en `n/a` il ne servirait que de bonus et
        # aucun bien ne pourrait mal noter. Ici le pavillon fait descendre.
        feats = flags.get("features") or []
        note, motifs = params.get("socle", 0.5), []
        if "authentique" in feats:
            note += 0.35; motifs.append("caractère / pierre")
        if flags.get("pavillon_neuf"):
            note -= 0.45; motifs.append("pavillon / lotissement / neuf")
        # « Trop ancien, trop rustique » (1★) : le cachet n'est un plus que si le bien
        # reste habitable. Une ruine de caractère n'en est pas un.
        cond = flags.get("condition")
        if cond in ("ruine", "gros_travaux"):
            note -= 0.25; motifs.append(f"état : {_COND_LABELS.get(cond, cond)}")
        return _clamp(note), "ok", " · ".join(motifs) or "rien de signalé"

    if kind == "commerces":
        n = flags.get("n_commerces")
        if n is None:
            return None, "pending", "commerces non vérifiés"
        ref = params.get("ref", 15)
        return _clamp(n / ref), "ok", f"{n} commerces/services à vélo (≤ 3 km)"

    if kind == "tension_locative":
        # Tension du marché locatif (demande > offre) : plus c'est tendu, mieux c'est
        # pour un investissement locatif (vacance faible, loyer soutenu).
        v = flags.get("tension_score")
        if v is None:
            return None, "n/a", "tension locative inconnue (commune hors référentiel)"
        return _clamp(v), "ok", f"tension locative {round(v * 100)}/100"

    if kind == "ski":
        if not flags.get("ski_checked"):
            return None, "pending", "ski non vérifié"
        d = flags.get("dist_ski_m")
        if d is None:
            return 0.15, "ok", "pas de remontée de ski à proximité"
        km = round(d / 1000, 1)
        max_km = params.get("max_km", 30)
        return _clamp(1 - km / max_km), "ok", f"remontée de ski à {km} km"

    if kind == "attractivite_airbnb":
        # « L'attractivité Airbnb comme un critère important. » Mesurée, pas lue dans
        # l'annonce : une annonce écrit « idéal investissement locatif » exactement comme
        # elle écrit « plein sud », c'est-à-dire sans l'avoir vérifié. Le barème est dans
        # `services/tourisme.py` ; ici on lit ce que l'échantillonnage OSM a relevé.
        from .tourisme import noter, resumer

        note = noter(flags)
        if note is None:
            return None, "pending", "attractivité locative non mesurée (réchauffage)"
        return note["note"], "ok", resumer(flags, note)

    if kind == "population_jeune":
        v = flags.get("pop_jeune_score")
        if v is None:
            return None, "pending", "données socio (enrich)"
        age = flags.get("age_median")
        return _clamp(v), "ok", f"âge médian {age} ans" if age else "population plutôt jeune"

    if kind == "orientation_gauche":
        v = flags.get("orientation_gauche_score")
        if v is None:
            return None, "pending", "données socio (enrich)"
        return _clamp(v), "ok", f"part gauche {round(v * 100)}%"

    if kind in _PENDING_KINDS:
        # Lit le champ d'enrichissement s'il existe déjà ; sinon pending.
        key = {"rail_time_from": "rail_time_min", "fiber": "fibre", "relief_mountain": "altitude", "hiking": "randonnee"}[kind]
        if key not in flags:
            return None, "pending", "provider non branché"
        val = flags[key]
        if kind == "rail_time_from":
            suffixe = " (estimé)" if flags.get("rail_time_estime") else ""
            return _clamp(1 - val / params.get("max_minutes", 180)), "ok", f"{val} min{suffixe}"
        if kind == "fiber":
            pct = flags.get("fibre_pct")
            if pct is not None:
                # Donnée Arcep au niveau COMMUNE (pas par logement) : à confirmer pour un
                # bien isolé/hameau, qui peut être dans la minorité non raccordée.
                return _clamp(pct / 100), "ok", f"{pct}% des locaux de la commune éligibles (à vérifier pour ce bien)"
            return (1.0 if val else 0.0), "ok", "fibre" if val else "pas de fibre"
        if kind == "relief_mountain":
            ref = params.get("ref_altitude", 600)
            return _clamp((val or 0) / ref), "ok", f"altitude {val} m (réf montagne {ref} m)"
        if kind == "hiking":
            n = flags.get("rando_count")
            if n is None:
                # Sans comptage, la donnée ne sait dire que oui/non.
                return (1.0 if val else 0.3), "ok", ("sentiers à proximité" if val else "peu de sentiers")
            peu = params.get("peu", _RANDO_PEU)
            beaucoup = params.get("beaucoup", _RANDO_BEAUCOUP)
            sub = _clamp((n - peu) / (beaucoup - peu)) if beaucoup > peu else (1.0 if n >= beaucoup else 0.0)
            return sub, "ok", f"{n} sentiers/itinéraires à proximité (repères {peu} à {beaucoup})"

    return None, "n/a", "inconnu"


# Les PALIERS ont été retirés le 5 septembre 2026. Ils plafonnaient un bien au palier
# quand une exigence n'était pas remplie — « pas de jardin prouvé, tu ne dépasses pas 70 ».
# Deux raisons de les supprimer, mesurées sur le catalogue têtard (3 046 biens) :
#
#  - ils écrasaient le classement : 127 biens exactement à la même valeur pour le set,
#    300 pour un profil « montagne » — un mur de scores identiques en haut de la liste ;
#  - ils ne protégeaient QUE le set. Sous les poids de quelqu'un d'autre, le même profil
#    montagne avait déjà 26 biens sous le plancher de prix et 4 ruines dans son top 50 :
#    les paliers ne filtraient pas, ils aplatissaient. Tant qu'ils étaient là, personne
#    ne pouvait avoir un classement vraiment différent de celui du set.
#
# Ce qu'ils portaient est repris ailleurs, à sa place :
#  - « critère mesuré exigé » -> l'a priori de `evaluate` (l'inconnu vaut la moyenne) ;
#  - « dans le budget », « pas de ruine » -> le sous-score du critère lui-même, qui tombe
#    franchement (voir `_budget_sub`) et que chacun peut pondérer ou re-seuiller.

def evaluate(item, preferences, apriori: dict[str, float] | None = None,
             ancres: tuple[float, float] | None = None) -> tuple[float | None, list[dict]]:
    """Calcule le match_score (0-100) et le détail par préférence.

    `apriori` (optionnel) : {libellé: sous-score moyen du catalogue}. Un critère NON
    MESURÉ sur ce bien reçoit cette valeur au lieu d'être retiré du dénominateur.

    C'est structurel, pas cosmétique. Sans a priori, `evaluate` renormalise sur les seuls
    critères notés : un bien dont l'exposition n'a jamais été calculée est jugé sans elle,
    donc sur un critère de poids 4 en moins — et il MONTE. Ne pas être mesuré devenait un
    avantage, que les paliers « attractivité mesurée » et « rapport qualité/prix mesuré »
    rattrapaient par une falaise. L'a priori le traite à la source : l'inconnu vaut la
    moyenne, ni mieux ni moins bien.
    """
    if not preferences:
        return None, []
    details = []
    total_w = 0.0
    acc = 0.0
    for pref in preferences:
        kind = getattr(pref, "kind", None) or (pref.get("kind") if isinstance(pref, dict) else None)
        weight = getattr(pref, "weight", None) if not isinstance(pref, dict) else pref.get("weight", 1.0)
        weight = 1.0 if weight is None else float(weight)
        params = getattr(pref, "params", None) if not isinstance(pref, dict) else pref.get("params", {})
        params = params or {}
        label = getattr(pref, "label", None) if not isinstance(pref, dict) else pref.get("label")

        sub, status, detail = _eval_one(item, kind, params)
        entry = {
            "kind": kind,
            "label": label or kind,
            "weight": weight,
            "status": status,
            "detail": detail,
            "subscore": round(sub, 3) if sub is not None else None,
        }
        if sub is not None and status == "ok":
            total_w += weight
            acc += weight * sub
            entry["contribution"] = round((weight * sub), 3)
        elif apriori is not None and (label or kind) in apriori:
            # Non mesuré : il compte quand même, à la valeur moyenne du catalogue.
            defaut = float(apriori[label or kind])
            total_w += weight
            acc += weight * defaut
            entry["apriori"] = round(defaut, 3)
        details.append(entry)

    if total_w == 0:
        return None, details
    basse, haute = ancres if ancres else (_ANCRE_BASSE, _ANCRE_HAUTE)
    score = round(_contraste(acc / total_w, basse, haute) * 100, 1)
    details.sort(key=lambda d: d.get("contribution", -1), reverse=True)
    return score, details
