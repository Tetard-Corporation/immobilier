from app.services.classify import classify


def test_detecte_ruine():
    res = classify("Terrain", "Ancien corps de ferme en ruine à réhabiliter")
    assert res["ruine"] is True
    assert res["a_renover"] is True  # une ruine est aussi à rénover


def test_detecte_a_renover_sans_ruine():
    res = classify("Maison", "Maison à rénover, prévoir des travaux")
    assert res["ruine"] is False
    assert res["a_renover"] is True


def test_bien_neuf():
    res = classify("Appartement neuf", "Bel appartement lumineux, cuisine équipée, proche commerces")
    assert res["ruine"] is False
    assert res["a_renover"] is False


def test_insensible_accents_casse():
    assert classify("À RÉNOVER ENTIÈREMENT")["a_renover"] is True
