from app.schemas import Preference
from app.services.brief import _heuristic_parse, parse_brief
from app.services.gares import nearest_gare
from app.services.geo import distance_to_corridor_km, resolve_city
from app.services.preferences import evaluate
from app.sources.base import NormalizedListing


def _listing(**kw):
    base = dict(source="x", external_id="1", type_bien="terrain")
    base.update(kw)
    return NormalizedListing(**base)


def test_geo_corridor_et_gare():
    paris, marseille = resolve_city("paris"), resolve_city("marseille")
    # Lyon est proche de l'axe Paris-Marseille
    d = distance_to_corridor_km(45.764, 4.835, [paris, marseille])
    assert d is not None and d < 60
    g = nearest_gare(45.764, 4.835)
    assert g is not None and g[1] < 10  # Lyon Part-Dieu tout proche


def test_budget_et_chambres():
    item = _listing(prix=120000, nb_chambres=5, surface_terrain=800, flags={})
    prefs = [
        Preference(kind="budget", weight=2, params={"apport": 150000, "levier": 1}),  # budget 150k
        Preference(kind="chambres_min", weight=2, params={"min": 5}),
        Preference(kind="has_terrain", weight=1),
    ]
    score, details = evaluate(item, prefs)
    assert score is not None and score >= 90
    assert all(d["status"] == "ok" for d in details)


def test_budget_depasse_baisse_le_score():
    cher = _listing(prix=400000, flags={})
    pas_cher = _listing(prix=100000, flags={})
    p = [Preference(kind="budget", params={"budget_max": 150000})]
    assert evaluate(cher, p)[0] < evaluate(pas_cher, p)[0]


def test_pending_quand_provider_absent():
    item = _listing(latitude=45.0, longitude=4.0, flags={})
    score, details = evaluate(item, [Preference(kind="fiber"), Preference(kind="rail_time_from", params={"ville": "Paris"})])
    statuses = {d["kind"]: d["status"] for d in details}
    assert statuses["fiber"] == "pending"
    assert statuses["rail_time_from"] == "pending"
    assert score is None  # aucune préférence évaluable -> pas de score


def test_feature_non_mentionnee_est_neutre():
    # Une feature non citée dans l'annonce ne doit PAS pénaliser (n/a, exclue du score).
    item = _listing(flags={"features": []})
    score, det = evaluate(item, [Preference(kind="feature", weight=2, params={"name": "cheminee"})])
    assert det[0]["status"] == "n/a"
    assert score is None
    item2 = _listing(flags={"features": ["cheminee"]})
    assert evaluate(item2, [Preference(kind="feature", params={"name": "cheminee"})])[0] == 100.0


def test_chambres_sous_minimum_degrade_lineaire():
    # Porte sur le SOUS-score : le match_score, lui, passe par l'étirement d'échelle.
    p = [Preference(kind="chambres_min", weight=1, params={"min": 4})]

    def sub(nb):
        return evaluate(_listing(nb_chambres=nb, flags={}), p)[1][0]["subscore"]

    assert sub(3) == 0.75   # 3/4
    assert sub(2) == 0.5    # 2/4
    assert sub(5) == 1.0    # >= min


def test_temps_acces_porte_a_porte():
    # Valence (sur l'axe, près du hub TGV) -> porte-à-porte court -> bon score.
    valence = _listing(latitude=44.93, longitude=4.89, flags={})
    p = [Preference(kind="temps_acces", params={"max_minutes": 240})]
    score, details = evaluate(valence, p)
    assert score is not None and details[0]["status"] == "ok"
    assert "porte-à-porte" in details[0]["detail"]
    # un point très loin de tout hub -> score plus faible
    brest = _listing(latitude=48.39, longitude=-4.48, flags={})
    assert evaluate(valence, p)[0] > evaluate(brest, p)[0]


def test_isole_renforce_par_densite():
    from app.enrichment.densite import isolement_score
    assert isolement_score(150) == 1.0
    assert isolement_score(20000) == 0.0
    assert 0 < isolement_score(2000) < 1
    # préférence feature=isole : commune peu peuplée -> bon score même sans mot-clé
    item = _listing(flags={"features": [], "isolement_score": 0.9, "population_commune": 180})
    score, det = evaluate(item, [Preference(kind="feature", params={"name": "isole"})])
    assert score >= 80 and "180 hab" in det[0]["detail"]


def test_brief_detecte_temps_acces():
    kinds = {p["kind"] for p in _heuristic_parse("Maison à 4h porte à porte de Paris, au calme")}
    assert "temps_acces" in kinds


def test_socio_preferences():
    item_data = _listing(latitude=48.85, longitude=2.35, flags={"pop_jeune_score": 0.8, "orientation_gauche_score": 0.6})
    p = [Preference(kind="population_jeune"), Preference(kind="orientation_gauche")]
    score, details = evaluate(item_data, p)
    assert score is not None and all(d["status"] == "ok" for d in details)
    # sans données socio -> pending
    item_vide = _listing(latitude=48.85, longitude=2.35, flags={})
    _, det = evaluate(item_vide, p)
    assert all(d["status"] == "pending" for d in det)


def test_brief_jeune_gauche():
    kinds = {p["kind"] for p in _heuristic_parse("commune jeune et à gauche, proche gare")}
    assert {"population_jeune", "orientation_gauche", "near_gare"} <= kinds


def test_corridor_paris_marseille_suit_la_vallee_du_rhone():
    # Un bien en vallée du Rhône (Valence) doit être bien plus proche de l'axe réel
    # qu'avec la ligne droite Paris-Marseille (qui passe par le Massif Central).
    from app.services.preferences import _corridor_points
    from app.services.geo import distance_to_corridor_km
    pts = _corridor_points({"villes": ["Paris", "Marseille"]})
    # Lyon, Valence, Avignon insérés -> au moins 6 points.
    assert len(pts) >= 6
    valence = (44.93, 4.89)
    d = distance_to_corridor_km(valence[0], valence[1], pts)
    assert d < 15  # sur l'axe rhodanien


def test_corridor_preference_score():
    lyon = _listing(latitude=45.764, longitude=4.835, flags={})
    brest = _listing(latitude=48.39, longitude=-4.48, flags={})
    p = [Preference(kind="near_corridor", params={"villes": ["Paris", "Marseille"], "max_km": 80})]
    assert evaluate(lyon, p)[0] > evaluate(brest, p)[0]


def test_brief_heuristique_exemple():
    brief = (
        "Sur l'axe Paris Marseille, plutôt isolé dans un coin nature montagneux pour des "
        "randonnées. À proximité d'une gare. Légers travaux possibles, avec du terrain. "
        "Sans vis à vis. Pour au moins 6 personnes en chambre. La fibre pour télétravailler. "
        "Le temps de trajet en train depuis Paris doit être raisonnable. Un terrain "
        "d'exception authentique. Budget via SCI avec 150000€ d'apports."
    )
    kinds = {p["kind"] for p in _heuristic_parse(brief)}
    assert {
        "budget",
        "chambres_min",
        "has_terrain",
        "light_works",
        "no_vis_a_vis",
        "nature_exception",
        "near_gare",
        "fiber",
        "relief_mountain",
        "hiking",
        "rail_time_from",
        "near_corridor",
    } <= kinds


def test_parse_brief_sans_cle_est_heuristique():
    res = parse_brief("Terrain avec fibre proche gare")
    assert res["parser"] == "heuristic"
    assert any(p["kind"] == "fiber" for p in res["preferences"])


def test_constructible():
    pref = [Preference(kind="constructible", weight=5)]

    def sub(flags):
        _, details = evaluate(_listing(prix=100000, surface_terrain=800, flags=flags), pref)
        return details[0]["subscore"], details[0]["status"]

    assert sub({"constructible": True, "zone_urba": "U"}) == (1.0, "ok")
    assert sub({"constructible": False, "est_zone_au": True}) == (0.6, "ok")   # zone AU
    assert sub({"constructible": False, "zone_urba": "N"}) == (0.1, "ok")      # non constructible
    assert sub({})[1] == "n/a"                                                  # zonage inconnu


def test_prix_m2_terrain():
    pref = [Preference(kind="prix_m2_terrain", weight=4, params={"bon": 80, "cher": 400})]

    def sub(prix, st):
        _, details = evaluate(_listing(prix=prix, surface_terrain=st, flags={}), pref)
        ss = details[0]["subscore"]
        return (round(ss, 3) if ss is not None else None), details[0]["status"]

    assert sub(40000, 1000) == (1.0, "ok")       # 40 €/m² -> excellent
    assert sub(200000, 500) == (0.25, "ok")      # 400 €/m² -> seuil « cher »
    assert sub(120000, 800)[0] < 1.0             # 150 €/m² -> intermédiaire
    assert sub(100000, None) == (None, "n/a")    # surface terrain inconnue


def test_prix_m2_terrain_discrimine_au_dela_du_seuil_cher():
    """Au-delà de « cher », l'ancien barème plafonnait : un terrain au tarif parisien
    obtenait la même note qu'un terrain simplement cher, et le critère ne triait plus."""
    pref = [Preference(kind="prix_m2_terrain", params={"bon": 80, "cher": 400})]

    def sub(ppm):
        _, details = evaluate(_listing(prix=ppm * 1000, surface_terrain=1000, flags={}), pref)
        return details[0]["subscore"]

    cher, tres_cher, parisien = sub(400), sub(600), sub(1000)
    assert cher > tres_cher > parisien
    assert parisien < 0.15  # prix parisien -> quasi éliminatoire


def test_budget_recompense_la_marge_sous_le_plafond():
    """« Je vise la bonne affaire » : rentrer tout juste dans le budget ne vaut pas
    autant que laisser de la marge."""
    pref = [Preference(kind="budget", params={"budget_max": 400_000})]

    def sub(prix):
        _, details = evaluate(_listing(prix=prix, flags={}), pref)
        return details[0]["subscore"]

    assert sub(200_000) == 1.0          # 50 % du budget -> pleine note
    assert sub(280_000) == 1.0          # 70 % -> encore pleine note
    assert sub(400_000) < sub(320_000)  # au plafond < avec de la marge
    assert sub(400_000) > sub(440_000)  # mais reste au-dessus du hors budget
    assert sub(560_000) == 0.0          # +40 % -> éliminé


def test_tranquillite_monte_et_descend():
    """Pris séparément, « sans vis-à-vis » / « isolé » ne sont cités que par 4 à 15 % des
    annonces : en `n/a` ils ne servaient que de bonus et aucun bien ne pouvait mal noter.
    Le critère composite doit être toujours évaluable et aller dans les deux sens."""
    pref = [Preference(kind="tranquillite")]

    def sub(flags):
        _, d = evaluate(_listing(flags=flags), pref)
        return d[0]["subscore"], d[0]["status"]

    neutre = sub({})[0]
    assert sub({})[1] == "ok"                                    # jamais n/a
    assert sub({"features": ["sans_vis_a_vis", "isole"]})[0] > neutre
    assert sub({"nuisances": ["vis_a_vis"]})[0] < neutre
    assert sub({"pavillon_neuf": True})[0] < neutre
    # Le pire cas (lotissement + vis-à-vis) doit vraiment tomber bas.
    assert sub({"pavillon_neuf": True, "nuisances": ["vis_a_vis"]})[0] == 0.0
    # Le meilleur cas doit atteindre le haut de l'échelle.
    assert sub({"features": ["sans_vis_a_vis", "isole", "calme"], "isolement_score": 1.0})[0] == 1.0


def test_coin_nature_priorise_l_eau():
    """« Une petite rivière c'est le must » : l'eau doit peser plus que les autres
    signaux, et deux bons signaux suffire pour la note pleine."""
    pref = [Preference(kind="coin_nature")]

    def sub(feats, alt=None):
        flags = {"features": feats}
        if alt is not None:
            flags["altitude"] = alt
        _, d = evaluate(_listing(flags=flags), pref)
        return d[0]["subscore"]

    assert sub([]) == 0.0                          # rien de cité -> 0, pas n/a
    assert sub(["eau"]) > sub(["arbore"])          # la rivière prime
    assert sub(["eau"]) > sub(["foret"])
    assert sub(["eau", "vue_panoramique"]) == 1.0  # rivière + vue dégagée = le cas idéal
    # « Vue dégagée car en hauteur » : l'altitude compte, plafonnée au point haut local.
    assert sub(["arbore"], alt=100) > sub(["arbore"], alt=10)
    assert sub([], alt=10) == 0.0                  # 10 m n'est pas une hauteur ici


def test_logement_compact_penalise_le_trop_grand():
    """« De la tiny house jusqu'à 3/4 chambres » : jamais pénaliser le petit, et
    continuer de décroître au-delà de la limite (5 et 8 chambres ne se valent pas)."""
    pref = [Preference(kind="logement_compact", params={"ideal": 3, "max": 4})]

    def sub(ch=None, pi=None, sb=None, type_bien="maison"):
        it = _listing(type_bien=type_bien, nb_chambres=ch, nb_pieces=pi, surface_bati=sb, flags={})
        _, d = evaluate(it, pref)
        return d[0]["subscore"], d[0]["status"], d[0]["detail"]

    assert sub(ch=1)[0] == 1.0 and sub(ch=3)[0] == 1.0     # tiny house = plein score
    assert sub(ch=4)[0] == 0.75                            # limite haute acceptée
    assert sub(ch=8)[0] < sub(ch=6)[0] < sub(ch=5)[0] < 0.75
    assert sub(ch=8)[0] > 0                                # décroissance, pas un mur à 0
    # Repli sur les pièces : mesuré sur 456 annonces, chambres ≈ pièces - 1.
    assert sub(pi=4)[0] == 1.0
    assert "pièces - 1" in sub(pi=4)[2]
    assert sub(pi=5)[0] == 0.75                            # T5 -> ~4 chambres
    assert sub(sb=90)[0] == 1.0                            # repli surface : compact
    assert sub(sb=300)[0] == 0.0
    assert sub(type_bien="terrain")[1] == "n/a"             # un terrain nu n'a pas de logement


def test_score_utilise_toute_l_echelle():
    """La moyenne pondérée d'une douzaine de critères se concentre au centre : sans
    étirement, 90 % des biens tombaient entre 50 et 79 et les pépites ne se
    détachaient pas. L'étirement est monotone : il étale sans changer le classement."""
    from app.services.preferences import _contraste

    assert _contraste(0.20) == 0.0 and _contraste(0.90) == 1.0
    assert _contraste(0.10) == 0.0 and _contraste(1.0) == 1.0   # borné
    # strictement croissant sur la plage utile
    vals = [_contraste(i / 100) for i in range(101)]
    assert all(b >= a for a, b in zip(vals, vals[1:]))
    assert round(_contraste(0.55), 6) == 0.5                     # milieu conservé au centre
    # un bien médiocre et un bien excellent doivent vraiment s'écarter
    pref = [Preference(kind="has_terrain", params={"min_surface": 1000})]
    petit = evaluate(_listing(surface_terrain=200, flags={}), pref)[0]
    grand = evaluate(_listing(surface_terrain=2000, flags={}), pref)[0]
    assert petit == 0.0 and grand == 100.0


def test_en_hauteur_geo():
    pref = [Preference(kind="en_hauteur_geo", weight=4)]

    def sub(prom):
        _, details = evaluate(_listing(prix=100000, flags={"prominence_m": prom} if prom is not None else {}), pref)
        ss = details[0]["subscore"]
        return (round(ss, 2) if ss is not None else None), details[0]["status"]

    assert sub(9)[0] == 1.0            # très dominant
    assert sub(6)[0] > sub(0)[0]      # surélevé mieux que plat
    assert sub(0) == (0.3, "ok")      # plat
    assert sub(-5)[0] < 0.1 or sub(-5)[0] == 0.0  # en creux -> bas
    assert sub(None) == (None, "n/a")  # relief non calculé


def test_distance_mer():
    pref = [Preference(kind="distance_mer", weight=5, params={"proche": 300, "loin": 3000})]

    def sub(dm):
        _, details = evaluate(_listing(prix=100000, flags={"dist_mer_m": dm} if dm is not None else {}), pref)
        ss = details[0]["subscore"]
        return (round(ss, 2) if ss is not None else None), details[0]["status"]

    assert sub(150) == (1.0, "ok")     # pieds dans l'eau
    assert sub(3500) == (0.1, "ok")    # loin
    assert sub(1000)[0] < 1.0 and sub(1000)[0] > 0.1
    assert sub(None) == (None, "n/a")  # non calculé


# --------------------------------------------------------------------------- #
# Exigences de palier : au-delà d'un score, certains critères deviennent requis.
# --------------------------------------------------------------------------- #
_EXIG_EAU = [{
    "above": 90,
    "label": "Vue ou contact avec l'eau",
    "requires": ["distance_mer", "bord_de_mer", "vue"],
    "mode": "any",
    "min_subscore": 0.6,
}]


def _prefs_haut(poids_mer=5):
    return [Preference(kind="budget", weight=5, params={"budget_max": 400000}),
            Preference(kind="has_terrain", weight=3),
            Preference(kind="distance_mer", weight=poids_mer,
                       params={"proche": 300, "loin": 3000})]


def _score_haut(flags, poids_mer=5, exigences=_EXIG_EAU):
    """Un bien qui score très haut sur les critères mesurés (donc au-dessus de 90).

    `poids_mer` faible = le score vient d'ailleurs (budget, terrain) : c'est le cas que
    les paliers visent, celui où un bien monte haut sans rien prouver sur l'eau.
    """
    item = _listing(prix=50000, surface_terrain=2000, flags=flags)
    return evaluate(item, _prefs_haut(poids_mer), exigences)


def test_exigence_laisse_passer_le_bien_qui_la_remplit():
    score, details = _score_haut({"dist_mer_m": 200})
    assert score > 90
    assert not [d for d in details if d["kind"] == "exigence"]


def test_exigence_plafonne_le_bien_loin_de_l_eau():
    """Score porté par le budget et le terrain, mer mesurée mais lointaine -> plafonné."""
    score, details = _score_haut({"dist_mer_m": 3500}, poids_mer=1)
    assert score == 90
    cap = [d for d in details if d["kind"] == "exigence"]
    assert cap and cap[0]["status"] == "ko" and "plafonné à 90" in cap[0]["detail"]


def test_exigence_non_validee_par_un_critere_jamais_mesure():
    """Sans mesure, rien ne prouve que le bien voit l'eau : le palier doit tenir."""
    score, details = _score_haut({})  # dist_mer_m absent -> statut n/a
    assert score == 90
    assert [d for d in details if d["kind"] == "exigence"]


def test_exigence_ignoree_sous_le_palier():
    """Un bien à 70 n'est pas concerné : l'exigence ne s'applique qu'au-dessus de 90."""
    pref = [Preference(kind="budget", weight=5, params={"budget_max": 100000})]
    item = _listing(prix=115000, flags={})  # hors budget -> score bas
    score, details = evaluate(item, pref, _EXIG_EAU)
    assert score is not None and score < 90
    assert not [d for d in details if d["kind"] == "exigence"]


def test_sans_exigences_le_score_est_inchange():
    """Le plafond ne doit exister que si le set le déclare : les autres sets ne bougent pas."""
    avec, _ = _score_haut({"dist_mer_m": 3500}, poids_mer=1)
    sans, _ = _score_haut({"dist_mer_m": 3500}, poids_mer=1, exigences=None)
    assert avec == 90 and sans > 90


def test_chambres_min_toujours_mesure():
    """Le critère capacité doit s'appliquer à TOUTES les annonces. Sans repli il était
    `n/a` — donc neutre — sur la moitié du lot têtard, et une maison d'une pièce a fini
    deuxième d'un classement qui exigeait quatre chambres."""
    pref = [Preference(kind="chambres_min", params={"min": 3})]

    def sub(**kw):
        _, d = evaluate(_listing(type_bien="maison", flags={}, **kw), pref)
        return d[0]["subscore"], d[0]["status"], d[0]["detail"]

    assert sub(nb_chambres=4)[0] == 1.0                  # au-dessus du minimum
    assert sub(nb_chambres=3)[0] == 1.0                  # pile au minimum
    assert sub(nb_chambres=1)[0] < sub(nb_chambres=2)[0] < 1.0  # dégradé, pas un mur
    # Repli 1 : les pièces (91 % des annonces, contre 51 % pour les chambres).
    assert sub(nb_pieces=5)[0] == 1.0 and "pièces - 1" in sub(nb_pieces=5)[2]
    assert sub(nb_pieces=1)[0] < 1.0                     # une pièce n'est pas une pépite
    # Repli 2 : la surface habitable, quand l'annonce ne donne ni chambres ni pièces.
    assert sub(surface_bati=140)[0] == 1.0
    assert sub(surface_bati=45)[0] < 1.0
    assert sub()[1] == "n/a"                             # plus rien à quoi se raccrocher


def test_rapport_qualite_prix_compare_au_secteur():
    """« Le rapport qualité/prix c'est essentiel » : le prix au m² du bien contre celui
    du secteur (DVF). C'est le ratio qui parle — 2 000 €/m² est cher en Ardèche."""
    pref = [Preference(kind="rapport_qualite_prix", params={"bon": 0.75, "cher": 1.7})]

    def sub(prix=None, bati=None, secteur=None):
        it = _listing(type_bien="maison", prix=prix, surface_bati=bati,
                      flags={"prix_m2_secteur": secteur} if secteur else {})
        _, d = evaluate(it, pref)
        return d[0]["subscore"], d[0]["status"]

    # 1 000 €/m² dans un secteur à 2 000 -> moitié prix -> plein score.
    assert sub(prix=100_000, bati=100, secteur=2000) == (1.0, "ok")
    # Au prix du secteur : note intermédiaire, ni bonne affaire ni excès.
    milieu = sub(prix=200_000, bati=100, secteur=2000)[0]
    assert 0.2 < milieu < 1.0
    # Deux fois le secteur, puis quatre fois : la note continue de descendre.
    assert sub(prix=400_000, bati=100, secteur=2000)[0] > sub(prix=800_000, bati=100, secteur=2000)[0]
    assert sub(prix=800_000, bati=100, secteur=2000)[0] > 0
    assert sub(prix=200_000, bati=100)[1] == "n/a"        # pas de référence DVF
    assert sub(prix=200_000, secteur=2000)[1] == "n/a"    # surface bâtie inconnue


def test_tranquillite_sans_isolement():
    """Le set têtard veut le calme SANS l'isolement (« pas isolé »). Poids nul = le
    signal sort du calcul, il ne compte pas pour zéro non plus."""
    sans_iso = [Preference(kind="tranquillite", params={"poids_isolement": 0, "poids_densite": 0})]
    defaut = [Preference(kind="tranquillite")]
    flags = {"features": ["isole"], "isolement_score": 1.0}

    def sub(pref, fl):
        _, d = evaluate(_listing(flags=fl), pref)
        return d[0]["subscore"], d[0]["detail"]

    assert sub(sans_iso, flags)[0] < sub(defaut, flags)[0]   # l'isolement ne rapporte plus
    assert "isolé" not in sub(sans_iso, flags)[1]            # ni ne s'affiche comme un plus
    # Le reste du critère continue de fonctionner dans les deux sens.
    assert sub(sans_iso, {"features": ["sans_vis_a_vis"]})[0] > sub(sans_iso, {})[0]
    assert sub(sans_iso, {"pavillon_neuf": True})[0] < sub(sans_iso, {})[0]
