from app.db import SessionLocal
from app.models import Listing
from app.schemas import SearchCriteria
from app.services.agences_ingest import _external_id, _to_normalized
from app.sources.agences import AgencesSource


def test_external_id_stable_par_url():
    a = _external_id("Agence", "https://x.fr/1", {})
    b = _external_id("Agence", "https://x.fr/1", {"prix": 1})  # l'URL prime
    assert a == b and a.startswith("ag_")


def test_to_normalized_porte_source_et_agence():
    item = _to_normalized(
        {"type_bien": "terrain", "prix": 50000, "code_postal": "33000", "url": "https://x.fr/1"},
        agency="Agence du Coin",
    )
    assert item.source == "agences"
    assert item.raw["agence"] == "Agence du Coin"


def test_agences_source_lit_la_base():
    db = SessionLocal()
    db.add(
        Listing(
            source="agences",
            external_id="ag_test_terrain",
            type_bien="terrain",
            prix=50000,
            surface_terrain=600,
            commune="Bordeaux",
            code_postal="33000",
            description="Terrain constructible",
            features=[],
            nuisances=[],
        )
    )
    db.commit()
    db.close()

    res = AgencesSource().search(
        SearchCriteria(property_types=["terrain"], code_postal="33000", prix_max=80000)
    )
    assert any(it.external_id == "ag_test_terrain" for it in res.items)


def test_agences_ingest_endpoint(client, monkeypatch):
    # Config vide + pas d'email -> ingestion à 0 (déterministe, sans réseau).
    from app.agences_config import AgencesConfig
    from app.services import agences_ingest

    monkeypatch.setattr(agences_ingest, "load_agences_config", lambda _p: AgencesConfig())
    monkeypatch.setattr(agences_ingest, "fetch_unseen", lambda _s: [])
    r = client.post("/api/agences/ingest")
    assert r.status_code == 200
    body = r.json()
    assert body["ingested"] == 0
    assert body["extractor"] == "heuristic"


# --------------------------------------------------------------------------- #
# Zone déclarée par agence : un réseau national partage un seul domaine, donc la
# récolte de liens sort de la région ; et une commune mal lue se géocode quand même.
# --------------------------------------------------------------------------- #
from app.agences_config import AgenceConfig, AgencesConfig  # noqa: E402


def test_departements_par_agence():
    cfg = AgencesConfig(agences=[
        AgenceConfig(nom="Bretonne", set_id=4, departements=["22", "29"]),
        AgenceConfig(nom="Sans zone", set_id=4),
    ])
    assert cfg.departements_par_agence == {"Bretonne": ["22", "29"]}
    assert cfg.set_par_agence == {"Bretonne": [4], "Sans zone": [4]}


def test_une_agence_peut_alimenter_un_set_et_son_sous_set():
    """Un bien rattaché au seul set parent disparaît du front dès qu'on bascule sur le
    sous-set, alors qu'il le concerne tout autant."""
    cfg = AgencesConfig(agences=[
        AgenceConfig(nom="Diois", set_ids=[1, 2]),
        AgenceConfig(nom="Ancienne écriture", set_id=4),
        AgenceConfig(nom="Sans set"),
    ])
    assert cfg.set_par_agence == {"Diois": [1, 2], "Ancienne écriture": [4]}


def test_departements_normalises_sur_deux_chiffres(tmp_path):
    """YAML lit `departements: [1, 22]` comme des entiers : « 1 » ne matcherait jamais
    un code postal, qui commence par « 01 »."""
    from app.agences_config import load_agences_config

    p = tmp_path / "agences.yaml"
    p.write_text('agences:\n  - nom: "A"\n    departements: [1, 22]\n', encoding="utf-8")
    assert load_agences_config(str(p)).agences[0].departements == ["01", "22"]


def test_code_postal_fait_foi_sur_la_commune(monkeypatch):
    """Vu en vrai : une annonce de Plougasnou (29630) est ressortie « Pourrières »,
    commune du Var, parce que la BAN géocode volontiers n'importe quel mot. Le code
    postal est structuré, la commune est du texte libre : le CP tranche."""
    from app.services import agences_ingest as ing

    monkeypatch.setattr("app.services.geo.geocode_locality",
                        lambda nom: {"nom": "Pourrières", "lat": 43.5, "lon": 5.7,
                                     "code_postal": "83910", "code_commune": "83097",
                                     "departement": "83"})
    monkeypatch.setattr("app.services.geo_communes.main_commune_for_postcode",
                        lambda cp: {"nom": "Plougasnou", "code": "29181",
                                    "lat": 48.69, "lon": -3.80})
    nl = ing.NormalizedListing(source="agences", external_id="x",
                               commune="Pourrières", code_postal="29630")
    out = ing._fill_geo(nl)
    assert out.commune == "Plougasnou" and out.departement == "29"


def test_commune_coherente_est_conservee(monkeypatch):
    """Quand la commune lue tombe dans le bon département, elle est plus précise que le
    « chef-lieu du code postal » : on la garde."""
    from app.services import agences_ingest as ing

    monkeypatch.setattr("app.services.geo.geocode_locality",
                        lambda nom: {"nom": "Locquénolé", "lat": 48.6, "lon": -3.87,
                                     "code_postal": "29670", "code_commune": "29134",
                                     "departement": "29"})
    monkeypatch.setattr("app.services.geo_communes.main_commune_for_postcode",
                        lambda cp: {"nom": "Taulé", "code": "29279",
                                    "lat": 48.57, "lon": -3.90})
    nl = ing.NormalizedListing(source="agences", external_id="x",
                               commune="Locquénolé", code_postal="29670")
    assert ing._fill_geo(nl).commune == "Locquénolé"
