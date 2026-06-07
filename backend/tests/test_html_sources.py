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


def test_seloger_parse_et_params():
    src = SeLogerSource()
    items = src._parse(_HTML, "terrain")
    assert len(items) == 1 and items[0].source == "seloger"

    params = src._params(SearchCriteria(property_types=["terrain", "maison"], prix_max=200000))
    assert set(params["types"].split(",")) == {"4", "2"}
    assert params["price"] == "NaN/200000"
    assert params["projects"] == "2"
