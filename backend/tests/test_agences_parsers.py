"""Tests des parsers d'agences locales (Voie B) + résolution de commune."""

from app.services import geo
from app.services.agences_parsers import parse_agence_cevenole, parse_site

# Carte minimale reproduisant la structure agencecevenole.com (réf AVANT le prix).
_HTML = """
<h2 class="headline-ann"><a href="details-maison+en+pierres+-+fay+sur+lignon-1503"
   title="Maison en pierres - Fay sur Lignon">Maison en pierres - Fay sur Lignon</a></h2>
<div class="reference"><span>Réf.</span><span class="text-ton">990</span></div>
<div class="prix"><span></span><span class="text-ton">115 000 €</span></div>
<div>Surface habitable <span class="text-ton">80</span> Surface terrain
   <span class="text-ton">245 m²</span> Jolie maison à rénover. En savoir plus</div>
<h2 class="headline-ann"><a href="details-terrain+a+batir+tence-1600" title="Terrain à bâtir Tence">x</a></h2>
<div class="prix"><span class="text-ton">40 000 €</span></div>
<div>Surface terrain <span>512</span> m² En savoir plus</div>
"""


def test_parse_cevenole_prix_non_colle_a_la_ref():
    items = parse_agence_cevenole(_HTML, "https://www.agencecevenole.com/ventes")
    assert len(items) == 2
    a = items[0]
    assert a["prix"] == 115000          # et NON 990115000 (réf collée)
    assert a["surface_bati"] == 80
    assert a["surface_terrain"] == 245
    assert a["type_bien"] == "maison"
    assert a["url"].endswith("/details-maison+en+pierres+-+fay+sur+lignon-1503")
    assert a["commune"] == "Maison en pierres - Fay sur Lignon"  # titre -> résolu via BAN ensuite
    assert items[1]["type_bien"] == "terrain" and items[1]["prix"] == 40000


def test_parse_site_dispatch_par_domaine():
    assert parse_site("https://www.agencecevenole.com/x", _HTML)          # parser enregistré
    assert parse_site("https://inconnue.fr/x", _HTML) == []              # domaine non géré


def test_geocode_locality_tokens_de_fin_et_abreviations(monkeypatch):
    geo._GEOCODE_CACHE.clear()
    # BAN simulée : "saint voy" matche Voyennes (homonyme), la fenêtre longue corrige.
    table = {
        "mazet saint voy": {"nom": "Mazet-Saint-Voy", "score": 0.9, "lat": 45.05, "lon": 4.3,
                            "code_postal": "43520", "code_commune": "43130", "departement": "43"},
        "saint voy": {"nom": "Voyennes", "score": 0.85, "lat": 49.7, "lon": 3.0,
                     "code_postal": "80400", "code_commune": "80811", "departement": "80"},
        "voy": None,
    }
    monkeypatch.setattr(geo, "_ban_municipality", lambda q: table.get(q))
    # "st voy" -> "saint voy" (expansion) ; fenêtre longue "mazet saint voy" l'emporte.
    g = geo.geocode_locality("tres belle ferme le mazet st voy")
    assert g["nom"] == "Mazet-Saint-Voy" and g["departement"] == "43"
