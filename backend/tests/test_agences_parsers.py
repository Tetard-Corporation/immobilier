"""Tests des parsers d'agences locales (Voie B) + résolution de commune."""

from app.services import geo
from app.services import agences_ingest as ing
from app.services.agences_parsers import (
    parse_agence_cevenole,
    parse_bauges_immobilier,
    parse_christine_miranda,
    parse_site,
)

# Cartes minimales reproduisant la structure agencecevenole.com (réf AVANT le prix,
# image dans le conteneur <div class="ann ...">).
_HTML = """
<div class="ann bord_b">
  <img data-src="public/img/medium/photo1.jpg" alt="Maison en pierres - Fay sur Lignon"/>
  <h2 class="headline-ann"><a href="details-maison+en+pierres+-+fay+sur+lignon-1503"
     title="Maison en pierres - Fay sur Lignon">Maison en pierres - Fay sur Lignon</a></h2>
  <div class="reference"><span>Réf.</span><span class="text-ton">990</span></div>
  <div class="prix"><span></span><span class="text-ton">115 000 €</span></div>
  <div>Surface habitable <span class="text-ton">80</span> Surface terrain
     <span class="text-ton">245 m²</span> Jolie maison à rénover. En savoir plus</div>
</div>
<div class="ann bord_b">
  <img src="public/img/medium/photo2.jpg" alt="Terrain à bâtir Tence"/>
  <h2 class="headline-ann"><a href="details-terrain+a+batir+tence-1600" title="Terrain à bâtir Tence">x</a></h2>
  <div class="prix"><span class="text-ton">40 000 €</span></div>
  <div>Surface terrain <span>512</span> m² En savoir plus</div>
</div>
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
    assert a["photos"] == ["https://www.agencecevenole.com/public/img/medium/photo1.jpg"]
    assert items[1]["type_bien"] == "terrain" and items[1]["prix"] == 40000


_BAUGES_HTML = """
<li class="property" data-property-id="87060860">
  <figure><a href="/fr/propriete/vente+maison+ecole+87060860">
    <img src="https://cdn.example.net/media/abc.jpg" alt="Grange à rénover à École"></a></figure>
  <article class="infos"><h3>Grange, École</h3>
    <h2>Grange à rénover à École (grande surface aménageable)</h2>
    <ul><li class="price">185 000 €</li><li><span class="area"></span>333 m²</li></ul></article>
</li>
"""


def test_parse_bauges_immobilier():
    items = parse_bauges_immobilier(_BAUGES_HTML, "https://bauges-immobilier.com/fr/ventes")
    assert len(items) == 1
    b = items[0]
    assert b["prix"] == 185000
    assert b["surface_bati"] == 333
    assert b["commune"] == "École"          # extrait de "Grange, École"
    assert b["type_bien"] == "maison"       # "Grange" -> maison
    assert b["url"].endswith("/fr/propriete/vente+maison+ecole+87060860")
    assert b["photos"] == ["https://cdn.example.net/media/abc.jpg"]


def test_parse_christine_miranda_liens_detail():
    html = ('<a href="details-ref+4550-+ferme+a+renover+suze+la+rousse-z117">x</a>'
            '<a href="details-ref+4550-+ferme+a+renover+suze+la+rousse-z117">dup</a>'
            '<a href="details-villa+avec+piscine+nyons-z200">y</a>')
    items = parse_christine_miranda(html, "https://www.christinemiranda.com/")
    assert len(items) == 2                       # dédoublonné
    assert items[0]["prix"] is None and items[0]["commune"] is None  # complétés via détail
    assert items[0]["type_bien"] == "maison"     # "ferme" -> maison
    assert items[0]["url"].endswith("/details-ref+4550-+ferme+a+renover+suze+la+rousse-z117")


def test_enrich_from_detail_garantit_photo_prix_commune(monkeypatch):
    # Page détail factice : og:image + prix + og:title avec indice de commune.
    detail = (
        '<meta property="og:image" content="https://cdn/x.jpg">'
        '<meta property="og:title" content="Ferme à rénover proche Suze-la-Rousse">'
        '<div class="prix">Prix : 350 000 €</div>'
    )

    class _R:
        text = detail

    monkeypatch.setattr(ing.httpx, "get", lambda *a, **k: _R())
    nl = ing._to_normalized(
        {"type_bien": "maison", "prix": None, "commune": None, "url": "https://x/d", "photos": []},
        "Christine Miranda",
    )
    out = ing._enrich_from_detail(nl)
    assert out.raw["photos"] == ["https://cdn/x.jpg"]     # photo garantie
    assert out.prix == 350000                              # prix depuis le détail
    assert "Suze" in (out.commune or "")                   # commune depuis og:title


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
