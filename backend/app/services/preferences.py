"""Moteur de préférences pondérées (régime ranking).

Aucune préférence n'exclut un bien : chacune produit un sous-score [0,1] (ou None si
non applicable / donnée manquante), agrégé en `match_score` [0,100] qui sert à classer.
Les préférences dépendant d'un enrichissement non encore branché (trajet train, fibre,
relief, rando) renvoient un statut `pending` tant que la donnée n'est pas disponible.
"""

from __future__ import annotations

from .gares import nearest_gare
from .geo import distance_to_corridor_km, haversine_km, resolve_city

# Préférences évaluables dès maintenant (annonce + géo) ; le reste = pending.
PREFERENCE_KINDS = [
    "budget",
    "chambres_min",
    "has_terrain",
    "constructible",
    "prix_m2_terrain",
    "en_hauteur_geo",
    "distance_mer",
    "surface_habitable",
    "light_works",
    "no_vis_a_vis",
    "tranquillite",
    "coin_nature",
    "logement_compact",
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
    "tension_locative",
    "ski",
    "population_jeune",
    "orientation_gauche",
    # Dépendent d'un provider d'enrichissement (Lot A) :
    "rail_time_from",
    "fiber",
    "relief_mountain",
    "hiking",
]

_PENDING_KINDS = {"rail_time_from", "fiber", "relief_mountain", "hiking"}
# Budget : part du budget en dessous de laquelle on est pleinement dans la « bonne
# affaire » (1.0), et note obtenue en consommant exactement 100 % du budget.
_BUDGET_CONFORT = 0.70
_BUDGET_LIMITE = 0.80
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
_LIGHT_OK = {"habitable": 1.0, "rafraichir": 1.0, "renover": 0.85, "gros_travaux": 0.4, "ruine": 0.1}
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
_ANCRE_BASSE = 0.20
_ANCRE_HAUTE = 0.90


def _contraste(x: float) -> float:
    return _clamp((x - _ANCRE_BASSE) / (_ANCRE_HAUTE - _ANCRE_BASSE))


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
        ratio = item.prix / budget
        if ratio <= _BUDGET_CONFORT:
            # On cherche la bonne affaire, pas le bien qui « rentre tout juste » : le
            # plein score est réservé à ce qui laisse de la marge (travaux, négociation).
            return 1.0, "ok", f"{int(item.prix)}€ — {round(ratio * 100)}% du budget {int(budget)}€"
        if ratio <= 1.0:
            sub = 1.0 - (ratio - _BUDGET_CONFORT) / (1.0 - _BUDGET_CONFORT) * (1.0 - _BUDGET_LIMITE)
            return sub, "ok", f"{int(item.prix)}€ — {round(ratio * 100)}% du budget {int(budget)}€ (haut de fourchette)"
        # Hors budget = quasi rédhibitoire (retour récurrent du groupe) -> pénalité forte :
        # +10% ~70%, +20% ~40%, +33% et plus ~0% de la note « au budget ».
        over = ratio - 1.0
        return _BUDGET_LIMITE * _clamp(1 - over * 3), "ok", f"{int(item.prix)}€ > budget {int(budget)}€ (+{round(over * 100)}%, hors budget)"

    if kind == "chambres_min":
        mn = params.get("min", 1)
        nb = item.nb_chambres
        if nb is None:
            return None, "n/a", "nb chambres inconnu"
        if nb >= mn:
            sub = 1.0
        else:
            # En dessous du minimum -> dégradé linéaire (et non un mur à 0) :
            # 3/4 = 0.75, 2/4 = 0.5, 1/4 = 0.25. Garde la direction (plus de chambres = mieux)
            # sans écraser le match des biens à rénover (souvent 2-3 ch).
            sub = _clamp(nb / mn)
        return sub, "ok", f"{nb} ch. (min {mn})"

    if kind == "has_terrain":
        if item.surface_terrain is None:
            return None, "n/a", "surface terrain inconnue"
        mn = params.get("min_surface", 1)
        seuil = f" (souhait ≥ {int(mn)} m²)" if mn and mn > 1 else ""
        return (1.0 if item.surface_terrain >= mn else _clamp(item.surface_terrain / mn)), "ok", f"{int(item.surface_terrain)} m²{seuil}"

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
        return _LIGHT_OK.get(cond, 0.6), "ok", f"état : {_COND_LABELS.get(cond, cond)}"

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
        feats, nuis = flags.get("features") or [], flags.get("nuisances") or []
        note, motifs = _TRANQ_SOCLE, []
        if "sans_vis_a_vis" in feats:
            note += 0.30; motifs.append("sans vis-à-vis")
        if "isole" in feats:
            note += 0.25; motifs.append("isolé / pleine nature")
        if "calme" in feats:
            note += 0.10; motifs.append("calme")
        iso = flags.get("isolement_score")
        if iso:
            note += 0.20 * iso
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
        vals = [d for d in (da, dr) if d is not None]
        if not vals:
            return 1.0, "ok", "aucune autoroute/voie ferrée à proximité"
        min_m = params.get("min_m", 200)
        ref_m = params.get("ref_m", 1000)
        sub = _clamp((min(vals) - min_m) / (ref_m - min_m))
        parts = []
        if da is not None:
            parts.append(f"autoroute {da} m")
        if dr is not None:
            parts.append(f"voie ferrée {dr} m")
        return sub, "ok", " · ".join(parts)

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
            detail = f"{n} sentiers/itinéraires à proximité" if n is not None else ("sentiers à proximité" if val else "peu de sentiers")
            return (1.0 if val else 0.3), "ok", detail

    return None, "n/a", "inconnu"


def _exigence_remplie(exig: dict, par_kind: dict) -> tuple[bool, str]:
    """Une exigence est-elle satisfaite ? Renvoie (ok, explication)."""
    requis = exig.get("requires") or []
    seuil = float(exig.get("min_subscore", 0.5))
    mode = exig.get("mode", "any")

    remplis, manquants = [], []
    for kind in requis:
        d = par_kind.get(kind)
        etiquette = (d or {}).get("label") or kind
        if d and d.get("status") == "ok" and (d.get("subscore") or 0) >= seuil:
            remplis.append(etiquette)
        else:
            # Un critère jamais mesuré (pending / n/a) ne peut pas valider une exigence :
            # sans la mesure, rien ne prouve que le bien la remplit.
            manquants.append(etiquette)

    if mode == "all":
        return (not manquants), ", ".join(manquants)
    return (bool(remplis)), ", ".join(manquants)


def appliquer_exigences(score: float | None, details: list[dict],
                        exigences: list[dict] | None) -> tuple[float | None, list[dict]]:
    """Plafonne le score tant qu'une exigence de palier n'est pas remplie.

    Un bien mal mesuré peut monter très haut par accident : `evaluate` renormalise sur les
    seuls critères notés, donc un bien dont trois critères sur dix-huit sont mesurés est
    jugé sur ces trois-là. Les paliers hauts servent à dire « au-delà de ce score, tel
    critère n'est plus optionnel » — sans mesure de la vue mer, un bien ne peut plus
    prétendre au haut du classement.

    Chaque exigence : {"above": 90, "requires": [kinds], "mode": "any"|"all",
    "min_subscore": 0.5, "label": "..."}. Le score est ramené au palier, jamais annulé.
    """
    if score is None or not exigences:
        return score, details
    par_kind = {d.get("kind"): d for d in details}
    for exig in sorted(exigences, key=lambda e: float(e.get("above", 0))):
        palier = float(exig.get("above", 0))
        if score <= palier:
            continue
        ok, manquants = _exigence_remplie(exig, par_kind)
        if ok:
            continue
        etiquette = exig.get("label") or f"Requis au-dessus de {palier:g}"
        details.append({
            "kind": "exigence", "label": etiquette, "weight": 0, "status": "ko",
            "subscore": None,
            "detail": f"plafonné à {palier:g} (score {score:g}) — manque : {manquants}"
                      if manquants else f"plafonné à {palier:g} (score {score:g})",
        })
        score = palier
    return score, details


def evaluate(item, preferences, exigences: list[dict] | None = None) -> tuple[float | None, list[dict]]:
    """Calcule le match_score (0-100) et le détail par préférence.

    `exigences` (optionnel) : paliers au-delà desquels certains critères deviennent
    obligatoires — voir `appliquer_exigences`.
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
        details.append(entry)

    if total_w == 0:
        return None, details
    score = round(_contraste(acc / total_w) * 100, 1)
    details.sort(key=lambda d: d.get("contribution", -1), reverse=True)
    return appliquer_exigences(score, details, exigences)
