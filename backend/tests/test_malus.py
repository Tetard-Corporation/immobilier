"""Une exigence pénalise ceux qui la ratent sans porter le score de ceux qui la tiennent.

Le défaut qu'elle répare : quatre critères du set têtard sont réussis par presque tout le
monde (format 0,96 de moyenne, jardin 0,94, chambres 0,92, surface 0,97). Dans une moyenne
pondérée, ils ajoutent à chaque bien presque la même chose — ils ne classent personne et
ils resserrent l'écart entre les biens.
"""

from app.schemas import Preference
from app.services.preferences import evaluate
from app.sources.base import NormalizedListing

# Ancres larges : on mesure l'effet du malus, pas l'écrasement par le clamp.
ANCRES = (0.20, 0.90)


def _listing(**kw):
    base = dict(source="x", external_id="1", type_bien="maison")
    base.update(kw)
    return NormalizedListing(**base)


def _prefs(avec_malus: bool):
    jardin = {"kind": "jardin", "weight": 4, "label": "Jardin", "params": {"min_surface": 300}}
    if avec_malus:
        jardin["malus"] = {"seuil": 0.5, "max": 0.45}
    return [
        {"kind": "has_terrain", "weight": 3, "label": "Terrain", "params": {"min_surface": 1000}},
        {"kind": "cachet", "weight": 4, "label": "Cachet", "params": {}},
        jardin,
    ]


def test_tenir_l_exigence_ne_rapporte_rien_la_rater_coute():
    avec = _listing(surface_terrain=1500, flags={"features": ["cachet"]})
    sans = _listing(surface_terrain=60, flags={"features": ["cachet"]})

    # Sans malus, le jardin compte dans la moyenne pour tout le monde.
    a_pond = evaluate(avec, _prefs(False), ancres=ANCRES)[0]
    s_pond = evaluate(sans, _prefs(False), ancres=ANCRES)[0]
    a_mal = evaluate(avec, _prefs(True), ancres=ANCRES)[0]
    s_mal = evaluate(sans, _prefs(True), ancres=ANCRES)[0]
    # Celui qui tient l'exigence n'est pas pénalisé : son score est celui des autres
    # critères, sans rien ajouter — c'est tout l'objet, ne plus porter le score de la
    # majorité qui coche.
    sans_jardin = [p for p in _prefs(True) if p["kind"] != "jardin"]
    assert a_mal == evaluate(avec, sans_jardin, ancres=ANCRES)[0]
    # Celui qui la rate paie, et il se retrouve RELATIVEMENT plus bas qu'avec un poids.
    # L'écart se lit en proportion et non en points : retirer de la moyenne un critère que
    # le bon bien réussissait fait aussi baisser ce dernier, et c'est précisément l'effet
    # recherché à l'échelle du catalogue — les ancres se recalibrent après (§4 ter).
    assert s_mal < s_pond
    assert s_mal / a_mal < s_pond / a_pond


def test_l_exigence_sort_de_la_moyenne():
    """Elle ne doit plus peser dans le dénominateur : sinon elle resserre encore."""
    bien = _listing(surface_terrain=1500, flags={"features": ["cachet"]})
    details = evaluate(bien, _prefs(True), ancres=ANCRES)[1]
    jardin = next(d for d in details if d["kind"] == "jardin")
    assert "contribution" not in jardin      # hors moyenne
    assert jardin["subscore"] is not None    # mais toujours mesuré et affiché
    assert "malus_applique" not in jardin    # exigence tenue : aucune sanction


def test_la_sanction_est_continue_et_ne_cree_pas_de_paquet():
    """Ce qui la distingue d'un palier : deux biens qui ratent gardent leur écart.

    Les paliers ont été retirés le 5 septembre parce qu'ils empilaient 117 biens
    exactement sur la même valeur. Un malus multiplicatif ne peut pas le faire.
    """
    p = _prefs(True)
    # Trois biens qui ratent tous l'exigence (jardin sous le seuil), à des degrés divers.
    faibles = [evaluate(_listing(surface_terrain=st, flags={"features": ["cachet"]}),
                        p, ancres=ANCRES)[0] for st in (40, 60, 80)]
    assert len(set(faibles)) == len(faibles)
    assert faibles[0] < faibles[-1]


def test_le_poids_regle_la_durete_de_la_sanction():
    """Le réglage personnel garde un sens : monter le poids durcit l'exigence."""
    sans_jardin = _listing(surface_terrain=60, flags={"features": ["cachet"]})
    doux = _prefs(True)
    dur = _prefs(True)
    dur[-1] = dict(dur[-1], weight=8, malus={"seuil": 0.5, "max": 0.45, "poids_ref": 4})
    doux[-1] = dict(doux[-1], malus={"seuil": 0.5, "max": 0.45, "poids_ref": 4})
    assert evaluate(sans_jardin, dur, ancres=ANCRES)[0] < evaluate(sans_jardin, doux, ancres=ANCRES)[0]


def test_un_critere_non_mesure_ne_declenche_aucune_sanction():
    """Une lacune n'est pas un manquement — c'est la règle de tout ce barème."""
    p = [{"kind": "cachet", "weight": 4, "label": "Cachet", "params": {}},
         {"kind": "fiber", "weight": 2, "label": "Fibre", "params": {},
          "malus": {"seuil": 0.9, "max": 0.5}}]
    bien = _listing(latitude=45.0, longitude=5.0, flags={"features": ["cachet"]})
    score, details = evaluate(bien, p, ancres=ANCRES)
    fibre = next(d for d in details if d["kind"] == "fiber")
    assert fibre["status"] == "pending"
    assert "malus_applique" not in fibre
    assert score == evaluate(bien, p[:1], ancres=ANCRES)[0]


def test_l_objet_schema_accepte_l_exigence():
    p = [Preference(kind="jardin", weight=4, params={"min_surface": 300},
                    malus={"seuil": 0.5, "max": 0.45})]
    d = evaluate(_listing(surface_terrain=60, flags={}), p, ancres=ANCRES)[1][0]
    assert d["malus_applique"] > 0
