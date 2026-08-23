from app.services.scoring import PILLARS, compute_score, scoring_schema


def _pillar(result, key):
    return next(p for p in result.pillars if p["key"] == key)


def _sub(pillar, key):
    return next(s for s in pillar["subpillars"] if s["key"] == key)


def test_structure_piliers_sous_piliers():
    res = compute_score({}, {"has_text": True})
    keys = {p["key"] for p in res.pillars}
    assert keys == {"prix", "foncier", "cadre", "risques", "etat", "accessibilite"}
    # chaque pilier porte ses sous-piliers
    prix = _pillar(res, "prix")
    assert {s["key"] for s in prix["subpillars"]} == {"niveau_prix", "affaire", "baisse_prix"}


def test_pending_nexclut_pas_le_calcul():
    # Sans enrichissement : affaire/zonage/risques/aérien/train/gare/fibre sont pending.
    res = compute_score({"price_decreased": True}, {"has_text": False, "surface_terrain": None})
    affaire = _sub(_pillar(res, "prix"), "affaire")
    assert affaire["status"] == "pending"
    # baisse_prix=ok=1.0 -> pilier prix présent, score global défini, pas écrasé par les pending
    assert _pillar(res, "prix")["score"] == 100.0
    assert res.score is not None


def test_cadre_nature_exception():
    flags = {"nature_score": 4, "nature_exception": True, "features": ["authentique"], "nuisances": []}
    res = compute_score(flags, {"has_text": True})
    cadre = _pillar(res, "cadre")
    assert cadre["score"] >= 90
    assert _sub(cadre, "exception")["subscore"] == 1.0


def test_enrichi_active_piliers_affaire_et_risques():
    flags = {
        "ecart_prix_pct": -20, "zone_urba": "U", "risques": [], "peb_zone": "A",
        "condition": "renover", "price_decreased": False, "nuisances": [], "nature_score": 1,
        "rail_time_min": 60, "fibre": True,
    }
    res = compute_score(flags, {"has_text": True, "surface_terrain": 800,
                                "latitude": 44.93, "longitude": 4.89})
    assert _sub(_pillar(res, "prix"), "affaire")["status"] == "ok"
    assert _sub(_pillar(res, "foncier"), "zonage")["subscore"] == 1.0  # zone U
    assert _sub(_pillar(res, "accessibilite"), "train")["status"] == "ok"
    # tous les piliers sont désormais évaluables
    assert all(p["score"] is not None for p in res.pillars)
    assert 0 <= res.score <= 100


def test_contributions_somme_coherente():
    flags = {"price_decreased": True, "condition": "habitable", "nuisances": [], "nature_score": 2}
    res = compute_score(flags, {"has_text": True})
    # somme des contributions des piliers ≈ score global
    total = round(sum(p["contribution"] for p in res.pillars), 1)
    assert abs(total - res.score) <= 0.5


def test_scoring_schema():
    schema = scoring_schema()
    assert {p["key"] for p in schema["pillars"]} == {p[0] for p in PILLARS}
    prix = next(p for p in schema["pillars"] if p["key"] == "prix")
    assert {s["key"] for s in prix["subpillars"]} == {"niveau_prix", "affaire", "baisse_prix"}


def test_niveau_prix_sanctionne_le_prix_absolu():
    """Un terrain au tarif parisien doit tomber bas même si le marché local est cher :
    « Affaire vs marché » seul déclarait bonne affaire tout ce qui était sous une
    référence locale elle-même hors de prix."""
    def sub(prix, surface_terrain):
        res = compute_score({}, {"has_text": True, "type_bien": "terrain",
                                 "prix": prix, "surface_terrain": surface_terrain})
        return _sub(_pillar(res, "prix"), "niveau_prix")["subscore"]

    assert sub(60_000, 1000) == 1.0        # 60 €/m² -> excellent
    assert sub(400_000, 1000) == 0.25      # 400 €/m² -> seuil « cher »
    assert sub(1_000_000, 1000) < 0.15     # 1000 €/m² -> prix parisien
    # bâti : barème distinct (€/m² habitable)
    res = compute_score({}, {"has_text": True, "type_bien": "maison", "prix": 200_000,
                             "surface_bati": 200})
    assert _sub(_pillar(res, "prix"), "niveau_prix")["subscore"] == 1.0  # 1000 €/m²


def test_niveau_prix_na_sans_surface():
    res = compute_score({}, {"has_text": True, "type_bien": "terrain", "prix": 300_000})
    assert _sub(_pillar(res, "prix"), "niveau_prix")["status"] == "n/a"


def test_affaire_ne_sature_plus_a_20_pct():
    """Avec l'ancienne bande de ±20 %, 74 % des terrains d'un même set sortaient à 1.0
    et le sous-pilier ne classait plus rien."""
    def sub(ecart):
        res = compute_score({"ecart_prix_pct": ecart}, {"has_text": True})
        return _sub(_pillar(res, "prix"), "affaire")["subscore"]

    assert sub(-40) > sub(-20) > sub(0) > sub(20) > sub(40)
    assert sub(0) == 0.5
    assert sub(-25) == 0.75


def test_prix_parisien_perd_contre_la_bonne_affaire():
    """Le cas concret qui a motivé le recalibrage : un terrain à 860 k€ / 865 €/m²
    ressortait en tête parce que le DVF local le disait « -66 % vs marché »."""
    commun = {"has_text": True, "type_bien": "terrain", "nature_score": 3,
              "zone_urba": "U", "risques": [], "condition": "habitable"}
    parisien = compute_score({"ecart_prix_pct": -66, **commun},
                             {**commun, "prix": 860_000, "surface_terrain": 994})
    affaire = compute_score({"ecart_prix_pct": -10, **commun},
                            {**commun, "prix": 105_000, "surface_terrain": 985})
    assert affaire.score > parisien.score
