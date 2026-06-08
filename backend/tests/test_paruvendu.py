from app.schemas import SearchCriteria
from app.sources.paruvendu import ParuvenduSource

_CARD = (
    '<div  class="blocAnnonce border-1" data-id="1282763849" data-cdp="" >'
    '<a href="/immobilier/vente/terrain/1282763849A1KIVHTE000" title=" Terrain - 4300 m&sup2;">'
    "<div>image</div></a>"
    "<span>100 000 &euro; * Terrain 4300 m 2 (23&euro;/m&sup2;) Priss&eacute; (71) 4 parcelles</span>"
    "</div>"
)


def test_parse_card():
    item = ParuvenduSource()._parse_card(_CARD, "terrain")
    assert item.source == "paruvendu"
    assert item.external_id == "1282763849"
    assert item.prix == 100000  # le prix au m² (23€) est écarté
    assert item.surface_terrain == 4300
    assert item.departement == "71"
    assert "Priss" in item.commune
    assert item.url.startswith("https://www.paruvendu.fr/immobilier/")
    assert item.url.endswith("/1282763849A1KIVHTE000")


def test_skip_card_sans_page_detail():
    # Carte "programme neuf" pointant vers une page catégorie (pas une annonce) -> ignorée.
    card = (
        '<div  class="blocAnnonce" data-id="999">'
        '<a href="/immobilier/maison-neuve/gironde-33/" title="Maisons neuves">promo</a>'
        "<span>200 000 &euro; Maison Gironde (33)</span></div>"
    )
    assert ParuvenduSource()._parse_card(card, "maison") is None


def test_path_par_type():
    src = ParuvenduSource()
    assert src._path(SearchCriteria(property_types=["terrain"])) == "/immobilier/vente/terrain/"
    assert src._path(SearchCriteria(property_types=["maison"], page=2)) == "/immobilier/vente/maison/?p=2"


def test_path_avec_departement():
    src = ParuvenduSource()
    assert src._path(SearchCriteria(property_types=["maison"], departement="07")) \
        == "/immobilier/vente/maison/ardeche-07/"
    assert src._path(SearchCriteria(property_types=["maison"], departement="26", page=2)) \
        == "/immobilier/vente/maison/drome-26/?p=2"


def test_prix_non_gonfle_par_compteur_photos():
    # Le nombre de photos ("12") précède le prix ("450 000 €") ; sur le texte aplati ils se
    # collent ("12 450 000"). Les balises les séparent dans le HTML -> prix correct.
    card = (
        '<div class="blocAnnonce" data-id="555">'
        '<a href="/immobilier/vente/maison/555000000A1" title="Maison 191 m&sup2;"></a>'
        '<span class="nb">12</span><span class="prix">450 000 &euro;</span>'
        "<span>Maison 191 m 2 Vernosc-lès-Annonay (07)</span></div>"
    )
    item = ParuvenduSource()._parse_card(card, "maison")
    assert item.prix == 450000          # et non 12 450 000
    assert item.departement == "07"
    assert item.commune == "Vernosc-lès-Annonay"   # pas "Maison Vernosc..."


def test_dept_slug():
    from app.services.departements import dept_slug
    assert dept_slug("07") == "ardeche-07"
    assert dept_slug("26") == "drome-26"
    assert dept_slug("2A") == "corse-du-sud-2A"
    assert dept_slug(None) is None
    assert dept_slug("999") is None
