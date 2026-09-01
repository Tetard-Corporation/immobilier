"""Test de l'export statique (dataset JSON) sans accès réseau (download_photos=False)."""

from __future__ import annotations

import json


def _seed(client):
    """Crée un set têtard + un bien réel via une recherche mock (persiste un Listing)."""
    client.post("/api/filter-sets", json={
        "name": "têtard-test",
        "criteria": {"preferences": [
            {"kind": "budget", "weight": 2, "params": {"budget_max": 300000}, "label": "Budget"},
            {"kind": "chambres_min", "weight": 1, "params": {"min": 3}, "label": "≥3 ch"},
        ]},
    })
    client.post("/api/search?source=mock&sort=score", json={"property_types": ["maison"]})


def test_export_build_dataset(client, tmp_path):
    _seed(client)
    from app.db import SessionLocal
    from app.services.export_static import build_dataset, export_to_dir

    db = SessionLocal()
    data = build_dataset(db, download_photos=False)

    assert {"generated_at", "sets", "biens", "searches", "stats"} <= data.keys()
    assert data["stats"]["n_biens"] == len(data["biens"])
    # un set avec préférences est exporté avec ses critères
    sets_named = {s["name"]: s for s in data["sets"]}
    assert "têtard-test" in sets_named
    assert len(sets_named["têtard-test"]["preferences"]) == 2

    # chaque bien porte un match recalculé pour le set, et la liste photos (vide ici)
    for b in data["biens"]:
        assert "scores_by_set" in b and "photos" in b
        assert b["photos"] == []  # pas de téléchargement réseau
    # la recherche mock a bien été tracée dans l'historique
    assert data["stats"]["n_searches"] >= 1

    # écriture sur disque
    stats = export_to_dir(db, str(tmp_path / "data"), download_photos=False)
    written = json.loads((tmp_path / "data" / "data.json").read_text(encoding="utf-8"))
    assert written["stats"] == stats


def test_pepites_gate():
    """Mode « pépites » : filtre le set primaire au seuil, préserve les autres sets."""
    from app.services.export_static import _passes_pepites_gate

    # Bien têtard (membre {1,2}) au-dessus du seuil -> gardé.
    sbs = {"1": {"match_score": 80.0}, "2": {"match_score": 60.0}}
    assert _passes_pepites_gate(sbs, {1, 2}, {1: 78}) is True
    # Même bien sous le seuil sur le set primaire -> écarté.
    assert _passes_pepites_gate({"1": {"match_score": 70.0}}, {1, 2}, {1: 78}) is False
    # Bien Pauline (membre {3}, pas du set primaire 1) -> toujours conservé.
    assert _passes_pepites_gate({"3": {"match_score": 40.0}}, {3}, {1: 78}) is True
    # Membre du set primaire mais non scoré dessus -> écarté (pas une pépite prouvée).
    assert _passes_pepites_gate({"2": {"match_score": 90.0}}, {1, 2}, {1: 78}) is False
    # member vide (rétro-compat "tous sets") + score suffisant -> gardé.
    assert _passes_pepites_gate({"1": {"match_score": 79.0}}, set(), {1: 78}) is True


def test_pepites_gate_plusieurs_sets():
    """La base garde tout le catalogue de chaque set, data.json n'en publie que le haut du
    panier : resserrer un seul set à l'export ferait revenir en bloc celui des autres."""
    from app.services.export_static import _passes_pepites_gate, _seuils_pepites

    seuils = {1: 78.5, 4: 80.0}
    # Chacun jugé sur SON set, pas sur celui de l'autre.
    assert _passes_pepites_gate({"1": {"match_score": 79.0}}, {1, 2}, seuils) is True
    assert _passes_pepites_gate({"4": {"match_score": 79.0}}, {4}, seuils) is False
    assert _passes_pepites_gate({"4": {"match_score": 81.0}}, {4}, seuils) is True
    # Un set non resserré n'est pas concerné.
    assert _passes_pepites_gate({"3": {"match_score": 10.0}}, {3}, seuils) is True
    # L'ancienne écriture reste acceptée et se fond dans la nouvelle.
    assert _seuils_pepites(78.0, 1, None) == {1: 78.0}
    assert _seuils_pepites(None, None, {1: 78.5, 4: 80.0}) == seuils
    assert _seuils_pepites(None, None, None) == {}


def test_temoin_overpass_demasque_une_instance_regionale(monkeypatch):
    """Une instance régionale répond 200 avec zéro élément sur un point français, et rien
    ne distingue cette réponse de « il n'y a pas de commerce ici ». Vécu : 850 biens neufs
    tous à zéro commerce, dont des bourgs à supermarché, sur un run déclaré réussi."""
    from app.services import export_static as E

    monkeypatch.setattr(E, "_query_poi", lambda lat, lon: {"n_commerces": 48})
    ok, msg = E.verifier_overpass("https://exemple/api")
    assert ok and "48" in msg

    monkeypatch.setattr(E, "_query_poi", lambda lat, lon: {"n_commerces": 0})
    ok, msg = E.verifier_overpass("https://suisse/api")
    assert not ok and "France" in msg

    monkeypatch.setattr(E, "_query_poi", lambda lat, lon: None)
    assert E.verifier_overpass("https://mort/api")[0] is False


def test_temoin_overpass_ne_change_pas_l_instance_courante(monkeypatch):
    """Vérifier une instance ne doit pas la rendre courante pour la suite de l'export."""
    from app.services import export_static as E

    avant = E._OVERPASS
    monkeypatch.setattr(E, "_query_poi", lambda lat, lon: {"n_commerces": 5})
    E.verifier_overpass("https://autre/api")
    assert E._OVERPASS == avant


def test_conserver_republie_un_set_a_l_identique():
    """Une correction de données déplace les scores. Recouper au passage le set d'un
    autre — 12 pépites bretonnes qui deviendraient 32 — n'est pas une décision qui se
    prend à l'export : on republie alors à l'identique."""
    from app.services.export_static import _passes_pepites_gate

    garder = {4: {("bienici", "A"), ("bienici", "B")}}
    seuils = {1: 85.0}
    sbs4 = {"4": {"match_score": 60.0}}   # score effondré : conservé quand même
    assert _passes_pepites_gate(sbs4, {4}, seuils, garder, ("bienici", "A")) is True
    sbs4b = {"4": {"match_score": 99.0}}  # score excellent : écarté s'il n'y était pas
    assert _passes_pepites_gate(sbs4b, {4}, seuils, garder, ("bienici", "Z")) is False
    # Le set gouverné par un seuil continue de l'être.
    assert _passes_pepites_gate({"1": {"match_score": 86.0}}, {1, 2}, seuils, garder, ("x", "y")) is True
    assert _passes_pepites_gate({"1": {"match_score": 84.0}}, {1, 2}, seuils, garder, ("x", "y")) is False


def _row_factice(**kw):
    from types import SimpleNamespace
    base = dict(source="bienici", external_id="A", type_bien="maison", prix=149000.0,
                latitude=44.87, longitude=4.62, surface_bati=140.0, surface_terrain=None,
                nb_pieces=6, nb_chambres=3, dpe_classe="d", condition="renover",
                code_commune="07048", commune="Chalencon", description="x" * 200, raw={})
    base.update(kw)
    return SimpleNamespace(**base)


def test_dedupe_export_fusionne_les_doublons_inter_sources():
    """Le dédoublonnage existait, mais seulement dans l'API de recherche live — jamais à
    l'export, qui produit pourtant le site. Chalencon occupait trois places dans les
    pépites publiées."""
    from app.services.export_static import _dedupe_rows

    bienici = _row_factice(source="bienici", external_id="iad-france-853388")
    # Les portails ne géolocalisent pas au même endroit : 100 m d'écart suffisaient à
    # casser l'empreinte, et Chalencon restait deux fois dans les pépites.
    leboncoin = _row_factice(source="leboncoin", external_id="3173025025",
                             latitude=44.8712, longitude=4.6215,
                             nb_chambres=None, description="y" * 90)
    gardes = _dedupe_rows([bienici, leboncoin])
    assert len(gardes) == 1
    # On garde la copie la PLUS COMPLÈTE, pas la mieux notée : un bien peu mesuré est
    # jugé sur les seuls critères qu'on a pu lui appliquer, donc il note plus haut.
    assert gardes[0].source == "bienici"


def test_dedupe_export_ne_fusionne_pas_deux_biens_voisins():
    """La géo à 110 m près ne distingue pas deux maisons mitoyennes de surfaces
    comparables. Le prix entre donc dans la clé : deux annonces du même bien portent le
    même prix, deux maisons voisines n'ont aucune raison d'être au même euro."""
    from app.services.export_static import _dedupe_rows

    a = _row_factice(external_id="A", prix=149000.0)
    b = _row_factice(external_id="B", prix=228000.0)
    assert len(_dedupe_rows([a, b])) == 2
    # Un écart de prix minime (arrondi de portail) reste un doublon.
    c = _row_factice(source="leboncoin", external_id="C", prix=149500.0)
    assert len(_dedupe_rows([a, c])) == 1


def test_dedupe_preserve_les_biens_deja_publies():
    """Les votes du groupe sont attachés au couple (source, external_id) : fusionner une
    copie publiée dans une autre la ferait disparaître du site et emporterait ses votes.
    Vécu : le set breton est passé de 12 à 11 pépites au premier export dédoublonné."""
    from app.services.export_static import _dedupe_rows

    complet = _row_factice(source="bienici", external_id="COMPLET")
    publie = _row_factice(source="leboncoin", external_id="PUBLIE", latitude=44.8712,
                          nb_chambres=None, description="court")
    # Sans consigne, la copie la plus complète gagne.
    assert _dedupe_rows([complet, publie])[0].external_id == "COMPLET"
    # Avec une identité déjà publiée, c'est elle qui reste — le lien des votes tient.
    garde = _dedupe_rows([complet, publie], preserver={("leboncoin", "PUBLIE")})
    assert len(garde) == 1 and garde[0].external_id == "PUBLIE"


def test_zone_du_set_filtre_sans_toucher_aux_biens():
    """La zone appartient au SET, pas aux biens. Le réflexe inverse — retirer le set des
    biens hors zone — se retourne contre soi : `set_ids` vide signifie « noté pour TOUS
    les sets », si bien que retirer 2 579 biens du set têtard les a fait noter aussi pour
    Pauline et la Bretagne."""
    from app.services.export_static import _dans_la_zone

    est = _row_factice(latitude=45.652, longitude=6.190)      # La Compôte (73)
    ouest = _row_factice(latitude=45.439, longitude=4.387)    # Saint-Étienne (42)
    sans_geo = _row_factice(latitude=None, longitude=None)
    zone = {"est_axe_lyon_valence": True}

    assert _dans_la_zone(est, zone) is True
    assert _dans_la_zone(ouest, zone) is False
    # Géoloc manquante : on n'écarte pas un bien sur une mesure absente.
    assert _dans_la_zone(sans_geo, zone) is True
    # Set sans zone déclarée : tout passe.
    assert _dans_la_zone(ouest, None) is True and _dans_la_zone(ouest, {}) is True


# --- Témoins de zone : le meilleur bien de chaque massif, même sous le seuil ----------
#
# Publier le seul haut du panier ne montre qu'une chose : les secteurs où le budget
# achète quelque chose. Le groupe n'a alors aucun moyen de comparer ce que 250 k€
# donnent en Tarentaise, au bord du Léman ou dans le Queyras.

_ZONES = [
    {"nom": "Beaufortain", "lat": 45.721, "lon": 6.575, "rayon_km": 30},
    {"nom": "Queyras", "lat": 44.700, "lon": 6.740, "rayon_km": 30},
    {"nom": "Diois", "lat": 44.754, "lon": 5.370, "rayon_km": 30},
]


def test_zone_de_rattache_au_massif_le_plus_proche():
    from app.services.export_static import _zone_de

    beaufort = _row_factice(latitude=45.72, longitude=6.57)
    die = _row_factice(latitude=44.75, longitude=5.37)
    # Entre deux massifs et hors de tous les rayons : pas de zone, donc pas de témoin.
    perdu = _row_factice(latitude=46.90, longitude=4.10)
    sans_geo = _row_factice(latitude=None, longitude=None)

    assert _zone_de(beaufort, _ZONES) == "Beaufortain"
    assert _zone_de(die, _ZONES) == "Diois"
    assert _zone_de(perdu, _ZONES) is None
    assert _zone_de(sans_geo, _ZONES) is None
    assert _zone_de(beaufort, None) is None


def _prep(cle, zone, score, member=frozenset({1})):
    return {"cle": cle, "member": set(member), "zones": {1: zone},
            "scores_by_set": {"1": {"match_score": score}}}


def test_un_seul_temoin_par_zone_le_mieux_note():
    from app.services.export_static import _meilleurs_par_zone

    prepares = [
        _prep(("bienici", "a"), "Beaufortain", 76.0),
        _prep(("bienici", "b"), "Beaufortain", 81.0),   # le meilleur du Beaufortain
        _prep(("bienici", "c"), "Queyras", 72.0),       # seul du Queyras
    ]
    assert _meilleurs_par_zone(prepares, {1: 70.0}) == {("bienici", "b"), ("bienici", "c")}


def test_un_bien_plafonne_pile_au_plancher_ne_peut_pas_etre_temoin():
    """`appliquer_exigences` ramène un bien recalé au palier EXACT : une ruine hors
    budget et sans jardin sort à 70,0 tout rond. Choisir le témoin avec « >= 70 »
    désignerait donc, dans les zones pauvres, le bien que les paliers viennent
    d'écarter — un cas vu sur les Aravis (grange en gros travaux, 70,0)."""
    from app.services.export_static import _meilleurs_par_zone

    plafonne = _prep(("bienici", "ruine"), "Aravis", 70.0)
    juste_au_dessus = _prep(("bienici", "ok"), "Verdon", 70.3)
    retenus = _meilleurs_par_zone([plafonne, juste_au_dessus], {1: 70.0})
    assert retenus == {("bienici", "ok")}


def test_une_zone_sans_rien_au_dessus_du_plancher_n_a_pas_de_temoin():
    """« Même si son score est bas » n'est pas « n'importe quoi » : sous 70, le bien a
    raté un palier — hors budget, rénovation complète, ou pas de jardin. Une zone qui
    n'a rien au-dessus reste vide, et cette absence est elle-même la réponse."""
    from app.services.export_static import _meilleurs_par_zone

    lignes = []
    prepares = [_prep(("bienici", "a"), "Queyras", 61.0),
                _prep(("bienici", "b"), "Diois", 88.0)]
    retenus = _meilleurs_par_zone(prepares, {1: 70.0}, log=lignes.append)

    assert retenus == {("bienici", "b")}
    assert "Queyras" in lignes[0] and "1 zones publiées" in lignes[0]


def test_temoin_ignore_les_biens_hors_du_set():
    from app.services.export_static import _meilleurs_par_zone

    prepares = [_prep(("bienici", "a"), "Diois", 95.0, member={3}),   # set Pauline
                _prep(("bienici", "b"), "Diois", 71.0)]
    assert _meilleurs_par_zone(prepares, {1: 70.0}) == {("bienici", "b")}


def test_export_publie_les_temoins_en_plus_des_pepites(client, tmp_path):
    """Bout en bout : un seuil que personne n'atteint, plus un témoin par zone."""
    from app.db import SessionLocal
    from app.models import FilterSet, Listing
    from app.services.export_static import build_dataset

    db = SessionLocal()
    fs = FilterSet(name="zones-test", criteria={
        # Deux critères, sinon un seul suffisant sature à 100 et aucun seuil ne trie.
        "preferences": [{"kind": "budget", "weight": 2, "label": "Budget",
                         "params": {"budget_max": 300000}},
                        {"kind": "surface_habitable", "weight": 3, "label": "Surface",
                         "params": {"min": 200}}],
        # Deux massifs : l'un a deux biens, l'autre un seul.
        "zones": [{"nom": "Beaufortain", "lat": 45.721, "lon": 6.575, "rayon_km": 25},
                  {"nom": "Queyras", "lat": 44.700, "lon": 6.740, "rayon_km": 25}],
    })
    db.add(fs)
    db.flush()
    for ext, lat, lon, prix, bati in (("beau-1", 45.72, 6.57, 240000.0, 100.0),
                                      ("beau-2", 45.73, 6.58, 120000.0, 140.0),
                                      ("quey-1", 44.70, 6.74, 235000.0, 90.0)):
        db.add(Listing(source="bienici", external_id=ext, type_bien="maison", prix=prix,
                       surface_bati=bati, latitude=lat, longitude=lon, commune=ext,
                       code_commune=ext, set_ids=[fs.id], raw={}))
    db.commit()

    strict = build_dataset(db, download_photos=False, pepites={fs.id: 95.0})
    avec_temoin = build_dataset(db, download_photos=False, pepites={fs.id: 95.0},
                                meilleur_par_zone={fs.id: 0.0})

    # (On ne compte pas le total : la base de test porte d'autres sets, que le
    # resserrage d'un set ne doit justement pas toucher.)
    def _miens(data):
        return {b["external_id"]: b for b in data["biens"]
                if b["external_id"] in ("beau-1", "beau-2", "quey-1")}

    assert _miens(strict) == {}
    assert avec_temoin["stats"]["n_temoins_zone"] == 2
    publies = _miens(avec_temoin)
    # Un témoin par massif, pas un par bien : le Beaufortain n'en donne qu'un, et c'est
    # le mieux noté (120 k€ pour 140 m² bat 240 k€ pour 100 m²).
    assert set(publies) == {"beau-2", "quey-1"}
    assert publies["beau-2"]["zone"] == "Beaufortain"
    assert publies["quey-1"]["zone"] == "Queyras"
    assert all(b["zone_temoin"] for b in publies.values())


def test_sous_compromis_detecte_sans_attraper_vendu_meuble():
    """Un bien sous compromis n'est plus à vendre : le montrer au groupe est pire qu'un
    viager, il n'y a même pas de décision à prendre. Le motif est étroit — « vendu »
    seul attrape « vendu meublé » et « vendu avec locataire en place »."""
    from app.services.export_static import _detect_sous_compromis

    for texte in ("SOUS COMPROMIS Maison familiale à Ugine",
                  "*** SOUS OFFRE *** Charmante maison de ville",
                  "Sous compromis : très bien située, entre la basilique et les haras",
                  "EXCLUSIF. DEJA SOUS COMPROMIS !!",
                  "Offre acceptée, visites suspendues"):
        assert _detect_sous_compromis(texte), texte

    for texte in ("VENDU AVEC LOCATAIRE EN PLACE, 489 euros de loyer",
                  "Les atouts : appartement refait à neuf, vendu meublé",
                  "Terrain proposé et vendu par notre partenaire foncier",
                  "La maison est vendue avec ses meubles"):
        assert not _detect_sous_compromis(texte), texte
