"""Les listes de codes postaux des zones de collecte.

Un code postal malformé ne lève aucune erreur : il est simplement demandé au portail,
qui ne renvoie rien. Le trou est donc invisible — c'est ainsi que Grenoble, Digne et
Briançon sont restés hors de la collecte Leboncoin et SeLoger pendant des semaines.

La cause : une virgule manquante en fin de bloc. Python concatène alors les deux
chaînes voisines, et « 01680 » suivi de « 04000 » devient « 0168004000 » — un code
postal de dix chiffres qui remplace les deux vrais. Sept occurrences, quatorze codes
postaux perdus.
"""

import pytest

from collect_leboncoin import PAULINE_ZIPS, PLOEMEUR_ZIPS, TETARD_ZIPS

ZONES = {"tetard": TETARD_ZIPS, "ploemeur": PLOEMEUR_ZIPS, "pauline": PAULINE_ZIPS}


@pytest.mark.parametrize("nom", sorted(ZONES))
def test_tous_les_codes_font_cinq_chiffres(nom):
    fautifs = [z for z in ZONES[nom] if len(z) != 5 or not z.isdigit()]
    assert not fautifs, (
        f"{nom} : {len(fautifs)} code(s) postal(aux) malformé(s) {fautifs}. "
        "Presque toujours une virgule manquante en fin de bloc — Python colle alors "
        "les deux chaînes voisines."
    )


@pytest.mark.parametrize("nom", sorted(ZONES))
def test_pas_de_doublon(nom):
    zips = ZONES[nom]
    doublons = sorted({z for z in zips if zips.count(z) > 1})
    assert not doublons, f"{nom} : code(s) postal(aux) en double {doublons}"


def test_la_zone_tetard_reste_a_l_est_du_rhone():
    # Le set a été resserré sur les Alpes : Ardèche (07), Loire (42) et Haute-Loire (43)
    # en sont sorties. Un code de ces départements qui réapparaît est une régression.
    dehors = sorted({z[:2] for z in TETARD_ZIPS} & {"07", "42", "43"})
    assert not dehors, f"départements à l'ouest de l'axe réintroduits : {dehors}"
