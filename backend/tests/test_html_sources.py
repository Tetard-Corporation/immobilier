"""Tests des sources HTML/JSON-LD (PAP, SeLoger) + utilitaires, hors-ligne."""

from app.schemas import SearchCriteria
from app.sources.htmlutil import json_ld_items, realestate_fields
from app.sources.pap import PapSource
from app.sources.seloger import SeLogerSource

_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
  {"@type":"Product","name":"Terrain à bâtir 800m2",
   "url":"https://www.pap.fr/annonce/terrains-bordeaux-r1234567",
   "offers":{"@type":"Offer","price":"85000","priceCurrency":"EUR"},
   "floorSize":{"@type":"QuantitativeValue","value":"800"},
   "address":{"@type":"PostalAddress","postalCode":"33000","addressLocality":"Bordeaux"},
   "geo":{"@type":"GeoCoordinates","latitude":"44.84","longitude":"-0.58"}}
]}
</script></head><body></body></html>
"""

# Carte de la SERP SeLoger (/classified-search), réduite à ce que le parseur lit.
# Les nombres portent les espaces fines insécables (U+202F) et insécables (U+00A0)
# réellement servis par le portail.
_SELOGER_SERP = """
<html><body>
<div id="classified-card-2544ZVNS9HPM" data-testid="serp-core-classified-card-testid">
  <a href="https://www.seloger.com/annonce/achat/bretagne/morbihan-56/ploemeur-56270/2544ZVNS9HPM"
     data-testid="card-mfe-covering-link-testid"
     title="Maison \u00e0 vendre - Ploemeur - 319\u202f000\u00a0\u20ac - 4 pi\u00e8ces, 3 chambres, 85,73 m\u00b2, 367,22 m\u00b2 de terrain"></a>
  <div data-testid="cardmfe-price-testid">319\u202f000\u00a0\u20ac 3\u202f721\u00a0\u20ac/m\u00b2</div>
  <div data-testid="cardmfe-keyfacts-testid">4 pi\u00e8ces \u00b7 3 chambres \u00b7 85,73 m\u00b2 \u00b7 367,22 m\u00b2 de terrain</div>
  <div data-testid="cardmfe-description-box-address">Keraude-Breuzent, Ploemeur (56270)</div>
  <div data-testid="cardmfe-description-text-test-id">Longère à rénover avec vue dégagée.</div>
  <div data-testid="card-mfe-energy-performance-class">D</div>
</div>
<div id="classified-card-26ZZTERRAIN01" data-testid="serp-core-classified-card-testid">
  <a href="https://www.seloger.com/annonce/achat/bretagne/morbihan-56/guidel-56520/26ZZTERRAIN01"
     data-testid="card-mfe-covering-link-testid"
     title="Terrain \u00e0 vendre - Guidel - 145\u202f000\u00a0\u20ac - 1\u202f250 m\u00b2 de terrain"></a>
  <div data-testid="cardmfe-price-testid">145\u202f000\u00a0\u20ac</div>
  <div data-testid="cardmfe-keyfacts-testid">1\u202f250 m\u00b2 de terrain</div>
  <div data-testid="cardmfe-description-box-address">Guidel (56520)</div>
</div>
</body></html>
"""


def test_json_ld_items_graph():
    items = json_ld_items(_HTML)
    assert len(items) == 1
    assert items[0]["name"].startswith("Terrain")


def test_realestate_fields():
    f = realestate_fields(json_ld_items(_HTML)[0])
    assert f["price"] == 85000
    assert f["surface"] == 800
    assert f["postal_code"] == "33000"
    assert f["city"] == "Bordeaux"
    assert f["latitude"] == 44.84


def test_pap_parse():
    items = PapSource()._parse(_HTML, "terrain")
    assert len(items) == 1
    it = items[0]
    assert it.source == "pap"
    assert it.external_id == "1234567"
    assert it.prix == 85000
    assert it.surface_terrain == 800
    assert it.code_postal == "33000"


def test_pap_path():
    assert PapSource()._path(SearchCriteria(property_types=["terrain"])) == "/annonce/vente-terrains"
    assert PapSource()._path(SearchCriteria(property_types=["maison"])) == "/annonce/vente-maisons"


def test_seloger_parse_serp():
    """Parsing d'une carte de la SERP (le JSON-LD par annonce n'existe plus)."""
    items = SeLogerSource._parse(_SELOGER_SERP)
    assert len(items) == 2
    maison, terrain = items

    assert maison.source == "seloger"
    assert maison.external_id == "2544ZVNS9HPM"
    assert maison.type_bien == "maison"  # « … m² de terrain » ne doit pas primer
    assert maison.prix == 319000
    assert maison.surface_bati == 85.73
    assert maison.surface_terrain == 367.22
    assert maison.nb_pieces == 4 and maison.nb_chambres == 3
    assert maison.commune == "Ploemeur" and maison.code_postal == "56270"
    assert maison.departement == "56"
    assert maison.dpe_classe == "d"
    assert maison.url.endswith("2544ZVNS9HPM")

    assert terrain.type_bien == "terrain"
    assert terrain.prix == 145000
    assert terrain.surface_terrain == 1250
    assert terrain.surface_bati is None  # « m² de terrain » n'est pas du bâti
    assert terrain.commune == "Guidel"


def test_seloger_params():
    src = SeLogerSource()
    params = src._params(SearchCriteria(property_types=["terrain", "maison"]), ["AD08FR22130"])
    assert set(params["estateTypes"].split(",")) == {"Plot", "House"}
    assert params["distributionTypes"] == "Buy"
    assert params["projectTypes"] == "Resale"  # exclut les programmes neufs
    assert params["locations"] == "AD08FR22130"
    assert "page" not in params  # page 1 implicite
    assert "priceMax" not in params and "priceMin" not in params  # aucun budget donné
    # Les bornes de prix (seuls filtres réellement appliqués côté serveur).
    budget = src._params(SearchCriteria(prix_min=50000, prix_max=400000.0), ["AD08FR1"])
    assert budget["priceMax"] == "400000" and budget["priceMin"] == "50000"
    assert src._params(SearchCriteria(), ["AD08FR1", "AD08FR2"], page=3)["page"] == "3"
    assert src._params(SearchCriteria(), ["AD08FR1", "AD08FR2"])["locations"] == "AD08FR1,AD08FR2"


def test_seloger_slug_et_nombres():
    from app.sources.seloger import _fr_num, _slug

    # Les URL SEO (qui portent le placeId) exigent un slug ASCII.
    assert _slug("Plœmeur") == "ploemeur"
    assert _slug("Clohars-Carnoët") == "clohars-carnoet"
    assert _slug("Saint-Étienne-de-Saint-Geoirs") == "saint-etienne-de-saint-geoirs"
    # Espaces fines insécables en séparateur de milliers, virgule décimale.
    assert _fr_num("319\u202f000\u00a0€") == 319000
    assert _fr_num("367,22 m²") == 367.22
    assert _fr_num(None) is None and _fr_num("—") is None


def test_seloger_available_gate():
    from app.config import Settings

    # Ni proxy ni cookie Datadome -> indisponible (comme Leboncoin, évite les 403).
    bare = SeLogerSource(settings=Settings(proxy_url="", seloger_datadome=""))
    assert bare.available is False
    assert "Cookie" not in bare._headers()
    # Cookie Datadome fourni -> disponible + cookie transmis.
    src = SeLogerSource(settings=Settings(proxy_url="", seloger_datadome="ABC123"))
    assert src.available is True
    assert src._headers()["Cookie"] == "datadome=ABC123"
    # L'UA du navigateur qui a généré le cookie est renvoyé quand il est connu
    # (Datadome recoupe cookie et empreinte de requête).
    with_ua = SeLogerSource(settings=Settings(seloger_datadome="ABC123", scraper_user_agent="UA/1.0"))
    assert with_ua._headers()["User-Agent"] == "UA/1.0"
    # Proxy résidentiel seul suffit aussi.
    assert SeLogerSource(settings=Settings(proxy_url="http://proxy:8000")).available is True


def test_seloger_search_sans_place_ne_appelle_rien(monkeypatch):
    """Sans commune résolvable, aucun appel réseau : SeLoger n'accepte pas de CP."""
    src = SeLogerSource()
    monkeypatch.setattr(src, "_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("appel réseau")))
    res = src.search(SearchCriteria(prix_max=400000))
    assert res.items == [] and res.total == 0
