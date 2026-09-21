"""Être à la montagne n'est pas être en altitude — les deux cas que le groupe a décrits."""

from app.schemas import Preference
from app.services.montagne import mesurer, noter, points_a_mesurer
from app.services.preferences import evaluate
from app.sources.base import NormalizedListing


def _listing(**kw):
    base = dict(source="x", external_id="1", type_bien="maison")
    base.update(kw)
    return NormalizedListing(**base)


def test_le_fond_de_vallee_alpin_bat_le_plateau_nu():
    """Le reproche, en une assertion.

    L'ancien critère notait l'altitude du bien sur une référence de 800 m : le village de
    fond de vallée cerné de sommets obtenait 0,38 et le plateau nu 1,00. C'est l'inverse
    de ce que le groupe appelle « être à la montagne ».
    """
    vallee = noter(300, 2000)      # fond de vallée, sommets à 2 000 m dans les 20 km
    plateau = noter(900, 1000)     # plateau à 900 m, rien au-dessus
    assert vallee > 0.8
    assert plateau < 0.3
    assert vallee > plateau
    # L'ancien barème disait exactement le contraire.
    assert 300 / 800 < 900 / 800


def test_la_hauteur_du_sommet_et_le_denivele_comptent_tous_les_deux():
    # Même dénivelé, sommet plus haut : mieux noté.
    assert noter(1000, 2200) > noter(200, 1400)
    # Même sommet, bien plus bas donc plus de relief sous les yeux : mieux noté.
    assert noter(200, 2000) > noter(1800, 2000)


def test_la_grille_fait_41_points_et_tient_en_deux_requetes():
    pts = points_a_mesurer(45.0, 5.5)
    assert len(pts) == 41
    assert pts[0] == (45.0, 5.5)
    # 24 altitudes par requête IGN groupée.
    assert -(-len(pts) // 24) == 2


def test_les_points_hors_zone_sont_ignores_mais_pas_le_site():
    # L'IGN renvoie -99999 en mer : un massif mesuré sur 30 points sur 40 reste mesuré.
    alt = [500] + [-99999] * 10 + [1800] * 30
    res = mesurer(alt)
    assert res["alt_max_20km_m"] == 1800 and res["amplitude_m"] == 1300
    # Sans altitude au site, il n'y a pas d'amplitude : on ne mesure rien plutôt que mal.
    assert mesurer([-99999] * 41) is None


def test_le_critere_lit_le_relief_mesure_et_retombe_sur_l_altitude_sinon():
    p = [Preference(kind="relief_mountain", params={"ref_altitude": 800})]
    mesure = {"altitude": 300, "alt_max_20km_m": 2000, "amplitude_m": 1700, "alt_site_m": 300}
    d = evaluate(_listing(flags=mesure), p)[1][0]
    assert d["subscore"] == noter(300, 2000)
    assert "2000 m dans les 20 km" in d["detail"]
    # Le critère RECALCULE au lieu de relire la note mise en cache : c'est ce qui permet
    # à chacun de régler son sommet de référence sans re-mesurer le relief.
    exigeant = [Preference(kind="relief_mountain", params={"ref_sommet": 4000})]
    assert evaluate(_listing(flags=mesure), exigeant)[1][0]["subscore"] < d["subscore"]
    # Non mesuré : l'ancien calcul, et le détail le dit.
    sans = evaluate(_listing(flags={"altitude": 300}), p)[1][0]
    assert sans["subscore"] < 0.5
    assert "non mesuré" in sans["detail"]
