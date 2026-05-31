from app.services.scoring import compute_score


def test_nature_exception_score_eleve():
    flags = {
        "niveau_travaux": 0,
        "nature_score": 4,
        "nature_exception": True,
        "nuisances": [],
        "price_decreased": False,
    }
    res = compute_score(flags, has_text=True)
    assert res.score >= 85
    keys = {c["key"] for c in res.components}
    assert {"nature", "etat", "nuisances", "prix_baisse"} == keys


def test_nuisances_font_baisser():
    base = {"niveau_travaux": 0, "nature_score": 1, "nuisances": [], "price_decreased": False}
    avec_nuisances = {**base, "nuisances": ["nuisances", "vis_a_vis"]}
    assert compute_score(avec_nuisances, has_text=True).score < compute_score(base, has_text=True).score


def test_composantes_lot_a_integrees_si_presentes():
    flags = {
        "ecart_prix_pct": -20,  # 20 % sous le marché
        "constructible": True,
        "risques": [],
        "peb_zone": "A",
        "niveau_travaux": 2,
        "nature_score": 1,
        "nuisances": [],
        "price_decreased": True,
    }
    res = compute_score(flags, has_text=True)
    keys = {c["key"] for c in res.components}
    assert {"affaire", "constructible", "risques", "aerien"} <= keys
    assert 0 <= res.score <= 100


def test_sans_texte_score_neutre():
    res = compute_score({"price_decreased": False}, has_text=False)
    assert res.score == 50.0  # seul 'prix_baisse' (baseline) compte
    assert [c["key"] for c in res.components] == ["prix_baisse"]
