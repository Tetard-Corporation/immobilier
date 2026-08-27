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


# --------------------------------------------------------------------------- #
# Voie C — générique : récolte des liens de fiches sur une page de liste.
# --------------------------------------------------------------------------- #
from app.services.agences_ingest import commune_depuis_titre  # noqa: E402
from app.services.agences_parsers import harvest_detail_links  # noqa: E402

_LISTE = """
<a href="/vente/41-henvic/maison/580-maison-henvic-120-m">Maison</a>
<a href="/vente/1-morlaix/immeuble/4158-immeuble-morlaix">Immeuble</a>
<a href="/a-vendre-propriete-bord-de-mer-19822.html">Propriété</a>
<a href="/estimation-gratuite-123.html">Estimation</a>
<a href="/nos-dernieres-ventes-10829.html">Vendus</a>
<a href="/contact">Contact</a>
<a href="/vente/1">Toutes nos ventes</a>
<a href="https://autre-site.fr/vente/maison-4242">Ailleurs</a>
"""


def test_harvest_garde_les_fiches():
    liens = harvest_detail_links(_LISTE, "https://agence.fr/vente/1")
    assert "https://agence.fr/vente/41-henvic/maison/580-maison-henvic-120-m" in liens
    assert "https://agence.fr/a-vendre-propriete-bord-de-mer-19822.html" in liens


def test_harvest_ecarte_les_pages_de_service():
    liens = harvest_detail_links(_LISTE, "https://agence.fr/vente/1")
    assert not [l for l in liens if "estimation" in l or "contact" in l
                or "dernieres-ventes" in l]


def test_harvest_reste_sur_le_domaine():
    """Suivre un lien hors site enverrait le robot sur un portail protégé."""
    liens = harvest_detail_links(_LISTE, "https://agence.fr/vente/1")
    assert not [l for l in liens if "autre-site.fr" in l]


def test_harvest_ignore_la_page_courante():
    liens = harvest_detail_links(_LISTE, "https://agence.fr/vente/1")
    assert "https://agence.fr/vente/1" not in liens


def test_harvest_respecte_le_plafond():
    html = "".join(f'<a href="/vente/maison/{i}-bien-{i}">x</a>' for i in range(50))
    assert len(harvest_detail_links(html, "https://agence.fr/liste", max_links=10)) == 10


def test_commune_depuis_titre_format_agence():
    assert commune_depuis_titre("Vente maison Henvic 6 pièces 120m²") == "Henvic"
    assert commune_depuis_titre("Vente maison Plourin-lès-Morlaix 6 pièces") == "Plourin-lès-Morlaix"
    assert commune_depuis_titre("Vente terrain Locquirec 969m²") == "Locquirec"


def test_commune_depuis_titre_refuse_de_deviner():
    """« proche Morlaix » n'est pas Morlaix : une commune fausse géocode quand même,
    et produit des coordonnées que plus rien ne corrige ensuite."""
    assert commune_depuis_titre("Vente maison de plain-pied proche Morlaix") is None
    assert commune_depuis_titre("Vente maison vue mer 5 pièces") is None
    assert commune_depuis_titre("") is None
