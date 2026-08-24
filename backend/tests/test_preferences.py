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


def test_near_sea_note_le_cote_mer():
    """Le long de la Laïta, la note doit décroître de l'embouchure vers l'amont ; les
    rias ne comptent pas comme littoral (sinon Pont-Scorff serait « bord de mer »)."""
    pref = [Preference(kind="near_sea", params={"max_km": 12})]

    def sub(lat, lon):
        _, details = evaluate(_listing(latitude=lat, longitude=lon, flags={}), pref)
        return details[0]["subscore"], details[0]["status"]

    pouldu = sub(47.7667, -3.5486)      # embouchure de la Laïta
    mi_laita = sub(47.8180, -3.5360)    # Saint-Maurice, à mi-cours
    quimperle = sub(47.8736, -3.5476)   # amont, confluence Ellé/Isole
    assert pouldu[0] > mi_laita[0] > quimperle[0]
    assert pouldu[0] > 0.95
    # Pont-Scorff est sur une ria, à ~11 km de l'océan : pas du bord de mer.
    assert sub(47.8320, -3.4030)[0] < 0.2
    # Hors emprise littorale connue -> non applicable, pas une distance inventée.
    assert sub(45.25, 5.02) == (None, "n/a")


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


def test_near_sea_naccepte_pas_de_penaliser_le_second_rang():
    """« La vue mer coûte très cher, je suis ok d'être en second rang » : tout ce qui est
    à portée du littoral sans être dessus garde le plein score."""
    pref = [Preference(kind="near_sea", params={"ok_km": 2.5, "max_km": 12})]

    def sub(lat, lon):
        _, d = evaluate(_listing(latitude=lat, longitude=lon, flags={}), pref)
        return d[0]["subscore"]

    assert sub(47.7667, -3.5486) == 1.0   # Le Pouldu, pieds dans l'eau
    assert sub(47.7930, -3.5820) == 1.0   # Clohars bourg, 2,5 km -> second rang, non pénalisé
    assert sub(47.8180, -3.5360) < 1.0    # mi-Laïta, 5,8 km -> décote
    assert sub(47.8736, -3.5476) < sub(47.8180, -3.5360)  # Quimperlé, plus loin encore


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
