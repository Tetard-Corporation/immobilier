"""L'entonnoir doit faire gagner du temps SANS perdre de pépite : ces tests verrouillent
les garde-fous, pas la performance."""

from types import SimpleNamespace

from app.services.entonnoir import appliquer, filtrer_par_commune, note_annonce


def _bien(code_commune="29000", description=None, prix=100000, terrain=800,
          type_bien="terrain", lat=48.6, lon=-3.8):
    return SimpleNamespace(code_commune=code_commune, description=description, prix=prix,
                           surface_terrain=terrain, type_bien=type_bien,
                           latitude=lat, longitude=lon)


def _distances(monkeypatch, table):
    monkeypatch.setattr("app.services.entonnoir.distance_mer_commune",
                        lambda items, **kw: table)


def test_note_annonce_penalise_le_pavillon_neuf():
    neuf = _bien(description="Pavillon neuf dans lotissement viabilisé")
    brut = _bien(description="Terrain nu")
    assert note_annonce(neuf) < note_annonce(brut)


def test_note_annonce_valorise_le_front_de_mer():
    assert note_annonce(_bien(description="Maison les pieds dans l'eau")) > \
           note_annonce(_bien(description="Maison au calme"))


def test_commune_lointaine_ecartee(monkeypatch):
    _distances(monkeypatch, {"29000": 25000})
    retenus, ecartes, _ = filtrer_par_commune([_bien(description="Terrain plat")], max_km=10)
    assert not retenus and len(ecartes) == 1


def test_bord_de_riviere_repeche_en_commune_lointaine(monkeypatch):
    """Le set note `bord_eau` (rivière, étang, ria) : la distance à la mer ne le mesure
    pas, donc elle ne doit pas écarter un bien qui l'annonce."""
    _distances(monkeypatch, {"29000": 25000})
    riviere = _bien(description="Belle parcelle en bord de rivière, au calme")
    retenus, ecartes, repeches = filtrer_par_commune([riviere], max_km=10)
    assert len(retenus) == 1 and not ecartes and repeches == 1


def test_commune_non_mesuree_est_retenue(monkeypatch):
    """Mieux vaut enrichir pour rien qu'écarter une pépite sur une mesure manquante."""
    _distances(monkeypatch, {})
    retenus, ecartes, _ = filtrer_par_commune([_bien(description="Terrain")], max_km=10)
    assert len(retenus) == 1 and not ecartes


def test_etage_commune_desactivable(monkeypatch):
    _distances(monkeypatch, {"29000": 25000})
    items = [_bien(description="Terrain plat")]
    assert len(appliquer(items, max_km=0, log=lambda *a: None)) == 1


def test_plafond_garde_les_meilleures_annonces(monkeypatch):
    _distances(monkeypatch, {"29000": 1000})
    faible = _bien(description="Maison de ville")
    fort = _bien(description="Terrain pieds dans l'eau avec vue mer imprenable")
    gardes = appliquer([faible, fort], max_km=10, garder=1, log=lambda *a: None)
    assert gardes == [fort]
