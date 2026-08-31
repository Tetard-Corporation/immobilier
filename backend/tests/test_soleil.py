"""Exposition et durée d'ensoleillement — calcul pur, sans réseau.

Les altitudes sont fabriquées ici : le module ne parle qu'en listes de nombres, et c'est
ce qui permet de tester la géométrie solaire sans dépendre de l'IGN.
"""

import math

from app.services.soleil import (
    AZIMUTS,
    DISTANCES,
    DECLINAISON_EQUINOXE,
    angle_masque,
    formater_heures,
    heures_de_soleil,
    mesurer,
    pente_et_exposition,
    points_a_mesurer,
    position_soleil,
    rose,
)

LAT = 45.5  # les Alpes du set têtard


def _altitudes(site=1000.0, pente_deg=0.0, azimut_pente=180.0, masque_deg=0.0):
    """Jeu d'altitudes cohérent avec `points_a_mesurer` : un versant incliné, entouré
    d'un horizon qui monte partout au même angle."""
    alt = [site]
    for k in range(8):
        a = math.radians(45 * k)
        # Projection de la direction du point sur la ligne de plus grande pente : le
        # terrain descend vers `azimut_pente`.
        vers_le_haut = -math.cos(a - math.radians(azimut_pente))
        alt.append(site + vers_le_haut * 90 * math.tan(math.radians(pente_deg)))
    for _ in AZIMUTS:
        for d in DISTANCES:
            alt.append(site + d * math.tan(math.radians(masque_deg)))
    return alt


def test_horizon_degage_donne_la_journee_entiere():
    """Sans relief, la durée mesurée est la durée astronomique : ~8h30 le 21 décembre à
    45,5° de latitude, 12h à l'équinoxe."""
    plat = {a: 0.0 for a in AZIMUTS}
    assert 8.0 < heures_de_soleil(LAT, plat) < 9.0
    assert abs(heures_de_soleil(LAT, plat, DECLINAISON_EQUINOXE) - 12.0) < 0.2


def test_ubac_ne_voit_pas_le_soleil_d_hiver():
    """Le soleil du 21 décembre culmine à 21,5° ici : un horizon barré à 25° l'efface
    complètement, alors que le même relief laisse passer l'équinoxe."""
    barre = {a: 25.0 for a in AZIMUTS}
    assert heures_de_soleil(LAT, barre) == 0.0
    assert heures_de_soleil(LAT, barre, DECLINAISON_EQUINOXE) > 5.0


def test_soleil_culmine_au_sud_a_midi():
    hauteur, azimut = position_soleil(LAT, 0.0, 0.0)
    assert abs(azimut - 180) < 0.5
    assert abs(hauteur - (90 - LAT)) < 0.5
    # Le matin, le soleil est à l'est (azimut < 180).
    assert position_soleil(LAT, 0.0, -45.0)[1] < 180


def test_exposition_lit_le_versant():
    az, pente = pente_et_exposition(1000.0, _altitudes(pente_deg=15, azimut_pente=180)[1:9])
    assert abs(az - 180) < 1 and abs(pente - 15) < 0.5
    assert rose(az) == "sud"
    # Sous 0,5°, l'orientation n'est plus qu'un bruit du modèle d'altitude : on ne
    # l'invente pas.
    az_plat, _ = pente_et_exposition(1000.0, _altitudes(pente_deg=0)[1:9])
    assert az_plat is None and rose(az_plat) == "terrain plat"


def test_mesurer_distingue_adret_et_ubac():
    adret = mesurer(LAT, _altitudes(pente_deg=20, azimut_pente=180, masque_deg=3))
    ubac = mesurer(LAT, _altitudes(pente_deg=20, azimut_pente=0, masque_deg=28))
    assert adret["exposition"] == "sud" and ubac["exposition"] == "nord"
    assert adret["soleil_hiver_h"] > 7 and ubac["soleil_hiver_h"] == 0.0
    assert adret["soleil_checked"] is True


def test_mesurer_refuse_un_point_sans_altitude():
    """Hors zone IGN, l'altitude vaut -99999 : on renvoie None plutôt qu'un versant
    inventé — un flag absent laisse le critère en `pending`, une valeur fausse ment."""
    alt = _altitudes()
    alt[0] = -99999.0
    assert mesurer(LAT, alt) is None
    assert mesurer(LAT, alt[:20]) is None  # lot IGN incomplet -> mesure positionnelle fausse


def test_masque_interpole_et_prolonge_les_bords():
    masque = {90: 0.0, 105: 10.0}
    assert abs(angle_masque(masque, 97.5) - 5.0) < 1e-6
    # Hors de l'arc mesuré, on prolonge le bord : supposer l'horizon dégagé là où on n'a
    # pas regardé ferait passer une combe pour un balcon.
    assert angle_masque(masque, 300) == 10.0


def test_points_a_mesurer_couvre_la_grille():
    pts = points_a_mesurer(LAT, 6.4)
    assert len(pts) == 1 + 8 + len(AZIMUTS) * len(DISTANCES)
    assert pts[0] == (LAT, 6.4)


def test_formater_heures():
    assert formater_heures(4.3) == "4h18"
    assert formater_heures(0.0) == "0h00"
