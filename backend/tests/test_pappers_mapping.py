"""Tests du mapping critères -> paramètres Pappers (sans réseau)."""

from app.schemas import SearchCriteria
from app.sources.pappers import criteria_to_params, estimate_credits, parse_parcelle


def test_mapping_filtres_geo_prix_surface():
    c = SearchCriteria(
        code_commune="33063", prix_max=200000, surface_terrain_min=300, nb_pieces_min=3
    )
    p = criteria_to_params(c, default_bases=["ventes"])
    assert p["code_commune"] == "33063"
    assert p["prix_vente_max"] == 200000
    assert p["surface_terrain_vente_min"] == 300
    assert p["nombre_pieces_vente_min"] == 3
    assert p["bases"] == "ventes"


def test_mapping_property_types():
    c = SearchCriteria(property_types=["terrain", "maison"])
    p = criteria_to_params(c, default_bases=[])
    assert "maison" in p["type_local_vente"]
    assert "vente_terrain_batir" in p["nature_vente"]


def test_estimate_credits():
    # 1 (parcelle) + 2 (ventes) = 3 crédits par parcelle.
    assert estimate_credits(["ventes"], 4) == 12


def test_parse_parcelle_extrait_vente_recente():
    parcelle = {
        "numero": "33063000AB0001",
        "commune": "BORDEAUX",
        "code_commune": "33063",
        "codes_postaux": ["33000"],
        "contenance": 250,
        "ventes": [
            {"id": "1", "date": "2019-01-01", "valeur_fonciere": 100000, "type_local": "maison"},
            {"id": "2", "date": "2023-06-01", "valeur_fonciere": 180000, "type_local": "maison",
             "surface_reelle_bati": 90, "surface_terrain": 250, "nombre_pieces": 4},
        ],
    }
    item = parse_parcelle(parcelle)
    assert item.prix == 180000  # vente la plus récente
    assert item.surface_bati == 90
    assert item.nb_pieces == 4
    assert item.commune == "BORDEAUX"
    assert item.code_postal == "33000"
