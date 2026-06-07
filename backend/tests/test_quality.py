from app.services.quality import classify_quality


def test_detecte_amenites_nature():
    res = classify_quality(
        "Terrain", "Vue panoramique imprenable, en pleine nature, au calme, joliment arboré"
    )
    assert {"vue", "isole", "calme", "arbore"} <= set(res["features"])
    assert res["nature_score"] >= 3
    assert res["nature_exception"] is True


def test_sans_vis_a_vis_nest_pas_une_nuisance():
    res = classify_quality("Maison sans vis-à-vis, exposition sud")
    assert "sans_vis_a_vis" in res["features"]
    assert "vis_a_vis" not in res["nuisances"]


def test_vis_a_vis_negatif():
    res = classify_quality("Appartement avec vis-à-vis, proximité autoroute bruyant")
    assert "vis_a_vis" in res["nuisances"]
    assert "nuisances" in res["nuisances"]
    assert res["nature_exception"] is False


def test_exception_requiert_amenite_forte():
    # Calme + ensoleillé + arboré : agréable mais pas d'aménité "forte" -> pas exception.
    res = classify_quality("Terrain au calme, ensoleillé, arboré")
    assert res["nature_exception"] is False


def test_vue_panoramique_distincte_de_vue_simple():
    pano = classify_quality("Maison", "Position dominante, vue panoramique imprenable sur la vallée")
    assert "vue_panoramique" in pano["features"]
    assert "vue" in pano["features"]  # panoramique implique vue

    simple = classify_quality("Maison", "Jolie maison avec vue sur le lac")
    assert "vue" in simple["features"]
    assert "vue_panoramique" not in simple["features"]


def test_vue_panoramique_synonymes():
    for txt in ["à flanc de coteau, belvédère", "vue à 360 degrés", "surplombant la vallée, horizon dégagé"]:
        assert "vue_panoramique" in classify_quality(txt)["features"], txt


def test_eau_riviere():
    res = classify_quality("Terrain en bord de rivière avec accès à l'étang")
    assert "eau" in res["features"]
