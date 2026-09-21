"""Accès depuis Paris : le référentiel des gares, le repli, et les deux critères.

Aucun test ne sort sur le réseau : la mesure (SNCF + itinéraire IGN) est réchauffée à
part et relue dans un cache, donc tout ce qui compte ici se teste hors ligne.
"""

from app.schemas import Preference
from app.services import trajet
from app.services.geo import porte_a_porte_min
from app.services.preferences import evaluate
from app.sources.base import NormalizedListing

GRENOBLE = (45.1913, 5.7143)
RENCUREL = (45.0833, 5.4333)   # Henri : « 1 h de route depuis la gare de Grenoble »


def _listing(**kw):
    base = dict(source="x", external_id="1", type_bien="maison")
    base.update(kw)
    return NormalizedListing(**base)


def test_referentiel_couvre_la_france_et_porte_les_durees_mesurees():
    gares = trajet._gares()
    # L'ancien fichier en comptait 89, choisies à la main.
    assert len(gares) > 2500
    hubs = [g for g in gares if g["paris_min"]]
    assert len(hubs) >= 40
    par_nom = {g["nom"]: g for g in gares}
    # Trois durées publiées par la SNCF, que personne ne doit pouvoir « arrondir ».
    assert 170 <= par_nom["Grenoble"]["paris_min"] <= 195
    assert 125 <= par_nom["Valence-TGV"]["paris_min"] <= 140
    assert par_nom["Valence-TGV"]["paris_gare"].startswith("Paris")
    # Une gare TER n'a PAS de durée depuis Paris : la SNCF n'en publie pas, et en
    # inventer une serait exactement le défaut qu'on répare.
    assert par_nom["Die"]["paris_min"] is None


def test_le_repli_est_plus_pessimiste_que_le_calcul_qu_il_remplace():
    """Le repli reste une estimation, mais il ne peut plus être trop optimiste.

    L'ancien porte-à-porte divisait le vol d'oiseau par 65 km/h ; la route demande en
    moyenne 1,23 fois ce temps (mesuré sur 37 biens du set). Le repli tourne donc à
    45 km/h — et il choisit parmi 45 gares au lieu de 9.
    """
    repli = trajet.repli_estime(*RENCUREL)
    assert repli["trajet_estime"] is True
    assert repli["trajet_paris"]["estime"] is True
    assert repli["porte_a_porte_min"] > porte_a_porte_min(*RENCUREL)


def test_le_cache_seul_repond_a_l_export():
    """L'export ne mesure jamais en direct : sans entrée en cache, il n'obtient rien."""
    assert trajet.acces(*RENCUREL, {}) == {}
    faux = {trajet.cle(*RENCUREL): {"porte_a_porte_min": 242, "acces_gare_min": 28}}
    assert trajet.acces(*RENCUREL, faux)["porte_a_porte_min"] == 242


def test_near_gare_compte_des_minutes_de_route_et_nomme_la_gare():
    mesure = {"acces_gare_min": 12,
              "trajet_paris": {"gare": "Grenoble", "type": "TGV", "paris_min": 181,
                               "voiture_min": 61, "total_min": 242,
                               "proche": {"gare": "St-Marcellin", "type": "TER",
                                          "voiture_min": 12}}}
    loin = dict(mesure, acces_gare_min=60)
    p = [Preference(kind="near_gare", params={"max_minutes": 45})]
    proche_d = evaluate(_listing(latitude=45.1, longitude=5.4, flags=mesure), p)[1][0]
    loin_d = evaluate(_listing(latitude=45.1, longitude=5.4, flags=loin), p)[1][0]
    assert proche_d["subscore"] > loin_d["subscore"]
    assert loin_d["subscore"] == 0.0
    assert "12 min de route" in proche_d["detail"]
    assert "St-Marcellin" in proche_d["detail"]


def test_temps_acces_decoupe_le_trajet_et_signale_l_estimation():
    mesure = {"porte_a_porte_min": 242,
              "trajet_paris": {"gare": "Grenoble", "paris_min": 181, "voiture_min": 61}}
    p = [Preference(kind="temps_acces", params={"max_minutes": 270})]
    d = evaluate(_listing(latitude=45.1, longitude=5.4, flags=mesure), p)[1][0]
    assert "Paris → Grenoble 181 min" in d["detail"]
    assert "61 min de voiture" in d["detail"]
    assert "(estimé)" not in d["detail"]
    # Sans mesure, le critère répond quand même — un critère absent serait EXCLU du
    # score au lieu de le baisser, et le bien non mesuré monterait.
    sans = evaluate(_listing(latitude=45.1, longitude=5.4, flags={}), p)[1][0]
    assert sans["status"] == "ok"
    assert "(estimé)" in sans["detail"]


def test_une_ruine_ne_vaut_plus_une_maison_habitable():
    """Le reproche du groupe, en une assertion : l'écart doit se voir dans le score."""
    p = [Preference(kind="light_works", weight=4),
         Preference(kind="has_terrain", weight=3, params={"min_surface": 1000})]
    habitable = _listing(surface_terrain=1500, flags={"condition": "habitable"})
    ruine = _listing(surface_terrain=1500, flags={"condition": "ruine"})
    assert evaluate(habitable, p)[0] - evaluate(ruine, p)[0] >= 40
