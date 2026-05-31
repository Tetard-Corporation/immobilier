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
