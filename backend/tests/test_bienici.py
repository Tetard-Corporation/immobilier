"""Tests du connecteur Bien'ici : normalisation et construction des filtres (offline)."""

from app.schemas import SearchCriteria
from app.sources.bienici import BienIciSource

# Extrait représentatif d'une annonce telle que renvoyée par realEstateAds.json.
_AD = {
    "id": "abc-123",
    "propertyType": "terrain",
    "adType": "buy",
    "price": [54000, 82000],
    "surfaceArea": None,
    "landSurfaceArea": [311, 564],
    "roomsQuantity": None,
    "city": "Saint-Hilaire-du-Rosier",
    "postalCode": "38840",
    "departmentCode": "38",
    "district": {"code_insee": "38394"},
    "title": "Terrain à bâtir",
    "description": "Ancienne grange à rénover, idéal investisseur.",
    "priceHasDecreased": True,
    "blurInfo": {"position": {"lat": 45.116998, "lon": 5.268266}},
    "publicationDate": "2026-05-01T10:00:00Z",
}


def test_normalisation_champs():
    item = BienIciSource._normalize(_AD)
    assert item.source == "bienici"
    assert item.external_id == "abc-123"
    assert item.type_bien == "terrain"
    assert item.prix == 54000  # borne basse d'une fourchette
    assert item.surface_terrain == 311
    assert item.commune == "Saint-Hilaire-du-Rosier"
    assert item.code_postal == "38840"
    assert item.code_commune == "38394"
    assert item.latitude == 45.116998 and item.longitude == 5.268266
    assert item.date_mutation == "2026-05-01"
    assert item.url.endswith("/abc-123")


def test_normalisation_flags():
    item = BienIciSource._normalize(_AD)
    # "grange à rénover" => niveau "renover" (2), distinct d'une ruine.
    assert item.flags["condition"] == "renover"
    assert item.flags["niveau_travaux"] == 2
    assert item.flags["price_decreased"] is True


def test_build_filters_mappe_types_et_bornes():
    src = BienIciSource()
    crit = SearchCriteria(
        property_types=["terrain", "maison"], prix_max=80000, surface_terrain_min=300, par_page=10
    )
    f = src._build_filters(crit)
    assert f["filterType"] == "buy"
    assert set(f["propertyType"]) == {"terrain", "house"}
    assert f["maxPrice"] == 80000
    assert f["minArea"] == 300
    assert f["size"] == 10
