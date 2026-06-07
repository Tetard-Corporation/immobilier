from app.services.dedup import dedupe, fingerprint
from app.sources.base import NormalizedListing


def _l(**kw):
    base = dict(source="x", external_id="1", type_bien="terrain")
    base.update(kw)
    return NormalizedListing(**base)


def test_meme_bien_deux_portails_fusionne():
    a = _l(source="bienici", external_id="1", prix=100000, surface_terrain=500, latitude=44.8378, longitude=-0.5792)
    b = _l(source="pap", external_id="x", prix=98000, surface_terrain=503, latitude=44.8378, longitude=-0.5793)
    assert fingerprint(a) == fingerprint(b)
    merged = dedupe([a, b])
    assert len(merged) == 1
    assert merged[0].prix == 98000  # garde le moins cher


def test_biens_distincts_non_fusionnes():
    a = _l(external_id="1", surface_terrain=500, latitude=44.8378, longitude=-0.5792)
    c = _l(external_id="2", surface_terrain=1200, latitude=43.60, longitude=1.44)
    assert fingerprint(a) != fingerprint(c)
    assert len(dedupe([a, c])) == 2


def test_fingerprint_sans_geo_utilise_code_postal():
    a = _l(latitude=None, longitude=None, code_postal="33000", surface_terrain=500)
    b = _l(latitude=None, longitude=None, code_postal="33000", surface_terrain=520)
    assert fingerprint(a) == fingerprint(b)
