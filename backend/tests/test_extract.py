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
