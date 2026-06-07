"""Recherche exhaustive par petit rayon (anti-biais des 100 annonces les plus récentes)."""

from app.services import geo_communes


def test_communes_within_filtre_et_trie(monkeypatch):
    fake = [
        {"nom": "Pivot", "code": "001", "lat": 45.0, "lon": 5.0},
        {"nom": "Proche", "code": "002", "lat": 45.09, "lon": 5.0},   # ~10 km
        {"nom": "Loin", "code": "003", "lat": 46.0, "lon": 5.0},      # ~111 km
    ]
    monkeypatch.setattr(geo_communes, "load_departement", lambda dep: fake)
    res = geo_communes.communes_within(45.0, 5.0, 15, ["73"])
    assert [c["code"] for c in res] == ["001", "002"]          # "Loin" exclu
    assert res[0]["dist_km"] == 0.0 and res[0]["dist_km"] <= res[1]["dist_km"]  # trié


def test_commune_zone_id_desambigue_par_proximite():
    from app.services.search import resolve_source

    src = resolve_source("bienici")
    # Deux homonymes : un lointain (Paris) en 1er, le bon (proche du centroïde) en 2e.
    src._suggest = lambda q: [  # type: ignore[method-assign]
        {"type": "city", "zoneIds": ["-99999"], "boundingBox": {"south": 48.79, "north": 48.81,
                                                                "west": 2.06, "east": 2.08}},
        {"type": "city", "zoneIds": ["-103519"], "boundingBox": {"south": 45.62, "north": 45.64,
                                                                "west": 6.17, "east": 6.19}},
    ]
    zid = src._commune_zone_id({"nom": "École", "code": "73106", "lat": 45.6276, "lon": 6.1816})
    assert zid == "-103519"  # retient le proche, pas l'homonyme parisien
