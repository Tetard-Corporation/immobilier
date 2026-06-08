"""Tests du connecteur Notaires de France (normalisation + filtres, offline)."""

from app.sources.notaires import NotairesSource

_AD = {
    "annonceId": 1997522,
    "reference": "07040-1095018",
    "statut": "LIGNE",
    "prixAffiche": 218000,
    "prixTotal": 218000,
    "surface": 61.0,
    "surfaceTerrain": 0,
    "nbPieces": 3,
    "nbChambres": 2,
    "communeNom": "Ruoms",
    "codePostal": "07120",
    "inseeCommune": "07201",
    "inseeDepartement": "07",
    "typeBien": "MAI",
    "typeTransaction": "VENTE",
    "bienVendu": "INCONNU",
    "bienRetire": "INCONNU",
    "descriptionFr": "Maison de village à rénover",
    "urlDetailAnnonceFr": "/fr/annonce-immo/ruoms-07/1997522",
}


def test_normalisation():
    it = NotairesSource._normalize(_AD)
    assert it.source == "notaires"
    assert it.external_id == "1997522"
    assert it.type_bien == "maison"
    assert it.prix == 218000
    assert it.surface_bati == 61.0
    assert it.surface_terrain is None          # 0 -> None
    assert it.nb_chambres == 2
    assert it.commune == "Ruoms" and it.departement == "07"
    assert it.code_commune == "07201"
    assert it.url == "https://www.immobilier.notaires.fr/fr/annonce-immo/ruoms-07/1997522"


def test_search_filtre_location_et_hors_ligne(monkeypatch):
    src = NotairesSource()
    location = {**_AD, "annonceId": 2, "typeTransaction": "LOCATION", "prixAffiche": 850}
    hors_ligne = {**_AD, "annonceId": 3, "statut": "RETIRE"}
    vendu = {**_AD, "annonceId": 4, "bienVendu": "OUI"}

    class _Resp:
        @staticmethod
        def json():
            return {"annonceResumeDto": [_AD, location, hors_ligne, vendu], "nbTotalAnnonces": 4}

    monkeypatch.setattr(src, "_get", lambda *a, **k: _Resp())
    from app.schemas import SearchCriteria

    res = src.search(SearchCriteria(departement="07", property_types=["maison"], par_page=50))
    ids = {it.external_id for it in res.items}
    assert ids == {"1997522"}   # location, hors-ligne et vendu écartés
