"""Tests du connecteur Leboncoin : payload et parsing (offline, sans réseau)."""

from app.schemas import SearchCriteria
from app.services.enrich import annotate
from app.sources.leboncoin import LeboncoinSource

_AD = {
    "list_id": 123456,
    "subject": "Terrain constructible 800m2",
    "body": "Beau terrain plat avec vue dégagée et sans vis-à-vis",
    "price": [85000],
    "first_publication_date": "2026-05-20 10:00:00",
    "url": "https://www.leboncoin.fr/ad/ventes_immobilieres/123456",
    "attributes": [
        {"key": "real_estate_type", "value": "3"},
        {"key": "land_plot_surface", "value": "800"},
        {"key": "bedrooms", "value": "3"},
        {"key": "rooms", "value": "5"},
        {"key": "square", "value": ""},
        {"key": "energy_rate", "value": "D"},
    ],
    "location": {"city": "Mérignac", "zipcode": "33700", "department_id": "33", "lat": 44.83, "lng": -0.65},
}


def test_normalisation():
    item = LeboncoinSource._normalize(_AD)
    assert item.source == "leboncoin"
    assert item.external_id == "123456"
    assert item.type_bien == "terrain"
    assert item.prix == 85000
    assert item.surface_terrain == 800
    assert item.surface_bati is None  # "" -> None
    # `bedrooms` n'était pas lu : les 2 698 biens leboncoin de la base sont entrés sans
    # nombre de chambres, et le set leur appliquait alors son repli « pièces - 1 »
    # — exact 46 % du temps, surestimé une fois sur deux.
    assert item.nb_pieces == 5
    assert item.nb_chambres == 3
    assert item.code_postal == "33700"
    assert item.departement == "33"
    assert item.latitude == 44.83 and item.longitude == -0.65
    assert item.date_mutation == "2026-05-20"
    assert item.url.endswith("/123456")


def test_normalisation_puis_annotation_nature():
    item = annotate(LeboncoinSource._normalize(_AD))
    assert "vue" in item.flags["features"]
    assert "sans_vis_a_vis" in item.flags["features"]


def test_headers_inclut_api_key_et_cookie_datadome():
    from app.config import Settings
    src = LeboncoinSource(settings=Settings(leboncoin_datadome="ABC123"))
    h = src._headers()
    assert h["api_key"]                       # clé requise par l'API
    assert h["Cookie"] == "datadome=ABC123"
    assert src.available is True              # cookie présent -> source utilisable


def test_indisponible_sans_proxy_ni_cookie():
    from app.config import Settings
    src = LeboncoinSource(settings=Settings(proxy_url="", leboncoin_datadome=""))
    assert src.available is False             # Datadome bloque l'IP datacenter
    assert "Cookie" not in src._headers()


def test_build_payload():
    src = LeboncoinSource()
    crit = SearchCriteria(
        property_types=["terrain", "maison"],
        prix_max=120000,
        surface_terrain_min=500,
        code_postal="33700",
        par_page=20,
    )
    p = src._build_payload(crit)
    assert p["filters"]["category"]["id"] == "9"
    assert set(p["filters"]["enums"]["real_estate_type"]) == {"3", "1"}
    assert p["filters"]["ranges"]["price"]["max"] == 120000
    assert p["filters"]["ranges"]["land_plot_surface"]["min"] == 500
    assert p["filters"]["location"]["city_zipcodes"] == [{"zipcode": "33700"}]
    assert p["limit"] == 20


def test_valeur_sentinelle_de_chambres_laissee_a_la_porte():
    """Leboncoin publie `999999` pour « non renseigné ». Entrée telle quelle, la valeur
    satisfait tous les seuils de chambres et s'affiche sur la carte du bien."""
    from app.sources.leboncoin import _chambres_plausibles

    assert _chambres_plausibles(999999) is None
    assert _chambres_plausibles(0) is None
    assert _chambres_plausibles(None) is None
    assert _chambres_plausibles(3) == 3
    assert _chambres_plausibles(16) == 16  # la plus grande du catalogue


def test_export_neutralise_une_sentinelle_deja_en_base():
    """Corriger la source ne répare pas les 7 000 lignes déjà collectées : l'export
    neutralise la valeur au moment de publier."""
    from app.models import Listing
    from app.services.export_static import _chambres

    assert _chambres(Listing(nb_chambres=999999)) is None
    assert _chambres(Listing(nb_chambres=4)) == 4
    assert _chambres(Listing(nb_chambres=None)) is None
