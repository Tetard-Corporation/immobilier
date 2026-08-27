from app.services.extract import HeuristicExtractor, get_extractor
from app.sources.htmlutil import html_to_text


def test_html_to_text_garde_les_liens():
    txt = html_to_text('<p>Terrain <a href="https://x.fr/1">voir</a></p>')
    assert "Terrain" in txt
    assert "https://x.fr/1" in txt


def test_heuristic_extrait_prix_surface_cp():
    ex = HeuristicExtractor()
    out = ex.extract(
        "Nouveau terrain",
        "Beau terrain de 800 m² à 85 000 € à Mérignac 33700",
    )
    assert len(out) == 1
    item = out[0]
    assert item["prix"] == 85000
    assert item["surface_terrain"] == 800
    assert item["code_postal"] == "33700"
    assert item["type_bien"] == "terrain"


def test_heuristic_email_sans_annonce():
    assert HeuristicExtractor().extract("Newsletter", "Bonne année à tous !") == []


def test_get_extractor_sans_cle_est_heuristique(monkeypatch):
    # Pas de clé Claude configurée dans les tests -> repli heuristique.
    assert get_extractor().name == "heuristic"


# --------------------------------------------------------------------------- #
# Voie D : lire une FICHE d'agence sans donnée structurée.
# --------------------------------------------------------------------------- #
from app.services.extract import HeuristicExtractor  # noqa: E402


def test_prix_avec_espace_fine_insecable():
    """U+202F sépare les milliers sur les sites récents ; l'ignorer lisait 0 €."""
    h = HeuristicExtractor
    assert h._to_float("1 189 000") == 1189000.0
    assert h._to_float("1 189 000") == 1189000.0
    assert h._to_float("1 189 000") == 1189000.0


def test_type_bien_vient_du_titre_pas_du_menu():
    """« terrain » traîne dans les menus de presque tous les sites : chercher dans la
    liste des types sur toute la page classait un hôtel particulier en terrain."""
    h = HeuristicExtractor
    assert h._type_bien("Hôtel particulier à vendre 10 pièces", "terrain maison") == "maison"
    assert h._type_bien("Vente Propriété de caractère", "terrain") == "maison"
    assert h._type_bien("Terrain constructible 900 m²", "") == "terrain"
    assert h._type_bien("", "") is None


def test_extraction_d_une_fiche_de_prestige():
    page = ("<html><body><h1>Hôtel particulier à vendre 10 pièces 280 m² "
            "1 189 000 € à LORIENT (56100)</h1>"
            "<nav>Terrains Maisons Appartements</nav></body></html>")
    out = HeuristicExtractor().extract("Hôtel particulier à vendre à LORIENT (56100)",
                                       page, is_html=True)
    assert len(out) == 1
    assert out[0]["prix"] == 1189000.0
    assert out[0]["code_postal"] == "56100"
    assert out[0]["type_bien"] == "maison"


def test_prix_ignore_la_taxe_fonciere():
    """Vu en vrai : « Taxe foncière : 571 € » avant le prix, et le bien entrait à 571 € —
    donc imbattable au budget et au €/m², donc pépite. Un prix faux est pire qu'absent."""
    h = HeuristicExtractor
    assert h._prix("Taxe foncière : 571 €\nPrix de vente 472 700 €") == 472700.0
    assert h._prix("Prix au m² : 3 560 €\nPrix 472 700 €") == 472700.0
    assert h._prix("Honoraires : 5 000 €\nCharges : 1 200 €") is None


def test_prix_rejette_les_montants_implausibles():
    h = HeuristicExtractor
    assert h._prix("Un café à 3 €") is None          # sous le plancher
    assert h._prix("Chiffre d'affaires 900 000 000 €") is None  # au-dessus du plafond
