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


def test_agences_ingest_endpoint(client):
    r = client.post("/api/agences/ingest")
    assert r.status_code == 200
    body = r.json()
    assert body["ingested"] == 0  # ni IMAP ni sites configurés en test
    assert body["extractor"] == "heuristic"
