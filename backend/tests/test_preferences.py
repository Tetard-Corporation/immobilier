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
    p = [Preference(kind="chambres_min", weight=1, params={"min": 4})]
    assert evaluate(_listing(nb_chambres=3, flags={}), p)[0] == 75.0   # 3/4
    assert evaluate(_listing(nb_chambres=2, flags={}), p)[0] == 50.0   # 2/4
    assert evaluate(_listing(nb_chambres=5, flags={}), p)[0] == 100.0  # >= min


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
