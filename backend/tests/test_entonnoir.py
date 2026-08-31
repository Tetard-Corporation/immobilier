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


# --------------------------------------------------------------------------- #
# Profil montagne (set têtard)
# --------------------------------------------------------------------------- #
def _maison(description=None, prix=250000, bati=140, terrain=1200, chambres=None,
            pieces=5, code_commune="26150", lat=44.75, lon=5.37):
    return SimpleNamespace(code_commune=code_commune, description=description, prix=prix,
                           surface_bati=bati, surface_terrain=terrain, type_bien="maison",
                           nb_chambres=chambres, nb_pieces=pieces, latitude=lat, longitude=lon)


def _altitudes(monkeypatch, table):
    monkeypatch.setattr("app.services.entonnoir.altitude_commune", lambda items: table)


def test_montagne_ecarte_le_logement_trop_petit():
    """Le trou par lequel une maison d'une seule pièce est arrivée deuxième d'un
    classement qui demandait quatre chambres : les pièces suppléent aux chambres."""
    from app.services.entonnoir import note_annonce_montagne

    assert note_annonce_montagne(_maison(pieces=1, bati=90)) < 0
    assert note_annonce_montagne(_maison(pieces=6, bati=160)) > 0
    # Les chambres, quand l'annonce les donne, priment sur l'estimation.
    assert note_annonce_montagne(_maison(chambres=1, pieces=6)) < note_annonce_montagne(_maison(pieces=6))


def test_montagne_ecarte_le_hors_budget_sans_appel_reseau():
    from app.services.entonnoir import note_annonce_montagne

    assert note_annonce_montagne(_maison(prix=900_000), prix_max=450_000) < -50


def test_montagne_penalise_les_gros_travaux():
    """« Pas le bâti ancien, ça veut dire travaux » : la ruine ne mérite pas 2,3 s
    d'enrichissement dans ce set."""
    from app.services.entonnoir import note_annonce_montagne

    assert note_annonce_montagne(_maison(description="Maison habitable de suite")) > \
           note_annonce_montagne(_maison(description="Ruine à reconstruire entièrement"))


def test_commune_de_plaine_ecartee(monkeypatch):
    from app.services.entonnoir import filtrer_par_altitude

    _altitudes(monkeypatch, {"26150": 120.0})
    retenus, ecartes, *_ = filtrer_par_altitude([_maison(description="Maison de village")],
                                                min_altitude=250)
    assert not retenus and len(ecartes) == 1


def test_riviere_ou_bois_repeche_en_plaine(monkeypatch):
    """L'altitude ne mesure ni une rivière ni un bois, et le set les note : une annonce
    qui les mentionne reste, même en commune basse."""
    from app.services.entonnoir import filtrer_par_altitude

    _altitudes(monkeypatch, {"26150": 120.0})
    riviere = _maison(description="Ancienne ferme au bord d'une rivière, en lisière de forêt")
    retenus, ecartes, repeches, _ = filtrer_par_altitude([riviere], min_altitude=250)
    assert len(retenus) == 1 and not ecartes and repeches == 1


def test_commune_non_mesuree_retenue(monkeypatch):
    """Mieux vaut enrichir pour rien qu'écarter une pépite sur une mesure manquante."""
    from app.services.entonnoir import filtrer_par_altitude

    _altitudes(monkeypatch, {})
    retenus, ecartes, *_ = filtrer_par_altitude([_maison(description="Maison")], min_altitude=250)
    assert len(retenus) == 1 and not ecartes


def test_etage_altitude_desactivable(monkeypatch):
    _altitudes(monkeypatch, {"26150": 120.0})
    monkeypatch.setattr("app.services.entonnoir.prix_m2_commune", lambda items, log=None: {})
    biens = [_maison(description="Maison de village")]
    assert len(appliquer(biens, profil="montagne", min_altitude=None, log=lambda *a: None)) == 1


def test_prix_au_m2_juge_par_rapport_au_marche_local():
    """Le prix au m² ne veut rien dire dans l'absolu. Mesuré : une coupe au prix absolu
    écartait Saint-François-Longchamp à 2 694 €/m² — « cher » — alors que le secteur y est
    à 3 700 et que le score en fait une pépite."""
    from app.services.entonnoir import note_annonce_montagne

    # `prix_max` explicite : le plafond du set est descendu à 250 k€, et un bien hors
    # budget est écarté avant même qu'on parle de prix au m². Ce n'est pas ce qu'on teste ici.
    bien = _maison(prix=299_000, bati=111, pieces=5)
    cher_ailleurs = note_annonce_montagne(bien, prix_max=300_000, reference_m2=1500)
    bonne_affaire = note_annonce_montagne(bien, prix_max=300_000, reference_m2=3700)
    assert bonne_affaire > cher_ailleurs
    # Sans référence, on ne tranche pas : le terme prix est neutre, pas pénalisant.
    sans = note_annonce_montagne(bien, prix_max=300_000)
    assert cher_ailleurs < sans < bonne_affaire


def test_communes_non_mesurees_signalees(monkeypatch):
    """Un étage qui échoue en silence ressemble trait pour trait à un étage qui ne trouve
    rien. Vécu : 25 communes mesurées sur plusieurs centaines, 3 biens écartés, aucun
    message — le run est passé pour propre."""
    from app.services.entonnoir import filtrer_par_altitude

    _altitudes(monkeypatch, {"26150": 800.0})   # une commune mesurée...
    biens = [_maison(code_commune="26150"), _maison(code_commune="73999"),
             _maison(code_commune="07001")]     # ...deux qui ne le sont pas
    retenus, ecartes, _, non_mesurees = filtrer_par_altitude(biens, min_altitude=250)
    assert len(retenus) == 3 and not ecartes
    assert non_mesurees == 2


def test_montagne_ecarte_la_maison_immense():
    """« 3/4 chambres max, pas des maisons immenses » : le plancher de capacité avait un
    pendant manquant. Sept chambres et 268 m² figuraient parmi les pépites publiées."""
    from app.services.entonnoir import note_annonce_montagne

    juste = note_annonce_montagne(_maison(chambres=4, bati=130, prix=200_000))
    immense = note_annonce_montagne(_maison(chambres=7, bati=268, prix=200_000))
    assert immense < juste


def test_montagne_accepte_un_peu_de_travaux():
    """« Un peu de travaux possible mais pas rénovation complète » : « à rénover » ne
    doit plus coûter comme un chantier lourd."""
    from app.services.entonnoir import note_annonce_montagne

    a_renover = note_annonce_montagne(_maison(description="Maison à rénover, beau volume"))
    complete = note_annonce_montagne(_maison(description="Rénovation complète à prévoir"))
    assert a_renover > complete
    assert a_renover > 0
