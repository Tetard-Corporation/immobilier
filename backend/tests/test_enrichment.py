"""Tests des providers d'enrichissement (parsing offline via httpx.MockTransport)."""

import httpx
import pytest

from app.enrichment import enrich_listing, get_providers, provider_status, reset_providers
from app.enrichment.base import EnrichmentProvider
from app.enrichment.georisques import GeorisquesProvider
from app.enrichment.gpu import GpuZonageProvider
from app.enrichment.rail import RailTimeProvider
from app.enrichment.relief import ReliefProvider
from app.sources.base import NormalizedListing


def _client(payload: dict) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))


def test_gpu_parse_zone_au():
    prov = GpuZonageProvider(client=_client({"features": [{"properties": {"typezone": "AU", "libelle": "1AU"}}]}))
    out = prov.enrich(45.0, 4.0)
    assert out["zone_urba"] == "AU"
    assert out["constructible"] is False
    assert out["est_zone_au"] is True


def test_georisques_parse_risques():
    payload = {
        "risquesNaturels": {"inondation": {"present": True}, "seisme": {"present": False}},
        "risquesTechnologiques": {"icpe": {"present": True}},
    }
    out = GeorisquesProvider(client=_client(payload)).enrich(45.0, 4.0)
    assert set(out["risques"]) == {"inondation", "icpe"}


def test_relief_parse_altitude():
    out = ReliefProvider(client=_client({"elevations": [850.0]})).enrich(45.0, 6.0)
    assert out["altitude"] == 850.0
    assert out["montagne"] is True


def test_rail_parse_minutes():
    assert RailTimeProvider._parse_minutes({"journeys": [{"duration": 7200}, {"duration": 5400}]}) == 90
    assert RailTimeProvider._parse_minutes({"journeys": []}) is None


def test_rail_indisponible_sans_cle():
    # Aucune clé Navitia dans les tests.
    assert RailTimeProvider().available is False
    assert {p["name"] for p in provider_status()} == {"gpu_zonage", "georisques", "relief", "rail_time"}


def test_enrich_listing_fusionne_et_recalcule_score():
    class _Dummy(EnrichmentProvider):
        name = "dummy"

        def _fetch(self, lat, lon):
            return {"constructible": True, "risques": [], "altitude": 900, "montagne": True}

    reset_providers([_Dummy()])
    try:
        item = NormalizedListing(
            source="x", external_id="1", type_bien="terrain", prix=120000,
            latitude=45.0, longitude=6.0, description="Terrain au calme", flags={},
        )
        enrich_listing(item)
        assert item.flags["constructible"] is True
        assert item.flags["altitude"] == 900
        assert item.flags["score"] is not None  # score recalculé avec l'enrichissement
    finally:
        reset_providers(None)  # restaure les providers réels


def test_enrich_sans_geoloc_ne_fait_rien():
    item = NormalizedListing(source="x", external_id="1", latitude=None, longitude=None, flags={})
    enrich_listing(item)
    assert "altitude" not in item.flags
