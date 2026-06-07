from app.services.classify import GROS_TRAVAUX, HABITABLE, RENOVER, RUINE, classify


def test_ruine_niveau_max():
    res = classify("Terrain", "Ancienne bâtisse en ruine, à reconstruire")
    assert res["condition"] == RUINE
    assert res["niveau_travaux"] == 4


def test_a_renover_distinct_de_ruine():
    res = classify("Maison", "Maison à rénover, travaux à prévoir")
    assert res["condition"] == RENOVER
    assert res["niveau_travaux"] == 2


def test_gros_travaux_plus_severe_que_renover():
    # Le niveau le plus sévère mentionné l'emporte.
    res = classify("Maison à rénover, prévoir de gros travaux et réhabilitation lourde")
    assert res["condition"] == GROS_TRAVAUX
    assert res["niveau_travaux"] == 3


def test_negation_aucun_travaux_non_classe_a_renover():
    res = classify("Maison", "Aucun travaux à prévoir, prête à habiter")
    assert res["condition"] == HABITABLE
    assert res["niveau_travaux"] == 0


def test_etat_inconnu():
    res = classify("Appartement", "Bel appartement lumineux proche commerces")
    assert res["condition"] is None
    assert res["niveau_travaux"] is None


def test_insensible_accents_casse():
    assert classify("RUINE À RECONSTRUIRE")["condition"] == RUINE
