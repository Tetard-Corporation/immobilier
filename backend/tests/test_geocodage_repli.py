"""Géocodage de repli : le centroïde communal quand la source ne donne pas de point.

Notaires et Paruvendu publient la commune, jamais de latitude/longitude. Sans ce repli,
les deux filtres qui définissent le set têtard sont inertes sur eux : `est_a_lest_du_rhone`
renvoie None (le bien est retenu par garde-fou) et l'étage altitude de l'entonnoir n'a
rien à mesurer. Mesuré sur une collecte réelle des deux sources : 182 biens sur 182
passaient sans être filtrés, et le haut du classement « montagne » était occupé par
Pierrelatte, Valence et Donzère — la vallée du Rhône, à 100 m et à l'ouest de l'axe.
"""

from app.services.enrich import annotate
from app.services.geo import est_a_lest_du_rhone
from app.services.geo_communes import coords_for_commune
from app.sources.base import NormalizedListing


def _bien(**kw) -> NormalizedListing:
    base = dict(source="notaires", external_id="x", type_bien="maison", prix=200_000)
    return NormalizedListing(**{**base, **kw})


# --- Le résolveur ---------------------------------------------------------------------

def test_nom_et_departement():
    coord = coords_for_commune("Le Bourg-d'Oisans", "38")
    assert coord is not None
    lat, lon = coord
    assert 45.0 < lat < 45.2 and 5.9 < lon < 6.2


def test_article_manquant():
    # Notaires publie « Échelles » et « Voulte-sur-Rhône » ; les communes s'appellent
    # « Les Échelles » et « La Voulte-sur-Rhône ». 9 % des communes alpines sont dans ce
    # cas, et Paruvendu n'a pas de code INSEE pour rattraper.
    assert coords_for_commune("Échelles", "73") == coords_for_commune("Les Échelles", "73")
    assert coords_for_commune("Bourg-d'Oisans", "38") == \
           coords_for_commune("Le Bourg-d'Oisans", "38")
    # L'article élidé aussi : `_sans_accents` garde l'apostrophe.
    assert coords_for_commune("Argentière-la-Bessée", "05") == \
           coords_for_commune("L'Argentière-la-Bessée", "05")


def test_un_alias_ne_masque_jamais_une_vraie_commune():
    # L'index ajoute « adrets » comme alias de « Les Adrets ». Si une commune s'appelle
    # réellement « Adrets », c'est elle qui doit répondre, pas l'alias.
    from app.services.geo_communes import _index_departement, _sans_accents

    for dep in ("38", "73", "05"):
        idx = _index_departement(dep)
        for cle, commune in idx.items():
            if cle.isdigit():          # entrée par code INSEE
                continue
            vrai = _sans_accents(commune["nom"])
            # Soit la clé est le nom exact, soit c'est un alias sans article de ce nom.
            assert cle == vrai or vrai.endswith(cle), f"{dep}: {cle!r} -> {commune['nom']!r}"


def test_code_insee_prime_sur_le_nom():
    # Le nom est faux, le code INSEE est celui de Die (26113) : c'est lui qui décide.
    coord = coords_for_commune("Commune Inexistante", "26", code_commune="26113")
    assert coord is not None
    lat, _lon = coord
    assert 44.7 < lat < 44.8


def test_accents_et_tirets_indifferents():
    assert coords_for_commune("saint jean de maurienne", "73") == \
           coords_for_commune("Saint-Jean-de-Maurienne", "73")


def test_commune_inconnue_ne_renvoie_rien():
    # Mieux vaut pas de coordonnées qu'une fausse : un bien sans point reste retenu par
    # les garde-fous de l'entonnoir, un point faux le range dans le mauvais massif.
    assert coords_for_commune("Zzzz-sur-Néant", "38") is None
    assert coords_for_commune(None, None) is None


# --- Le branchement dans annotate ------------------------------------------------------

def test_annotate_pose_le_centroide_et_le_signale():
    it = annotate(_bien(commune="Le Bourg-d'Oisans", departement="38"))
    assert it.latitude is not None and it.longitude is not None
    assert it.flags.get("position_commune") is True


def test_annotate_ne_remplace_jamais_un_point_donne_par_la_source():
    it = annotate(_bien(commune="Le Bourg-d'Oisans", departement="38",
                        latitude=44.0, longitude=5.0))
    assert (it.latitude, it.longitude) == (44.0, 5.0)
    assert "position_commune" not in it.flags


def test_le_filtre_est_du_rhone_tranche_enfin():
    # Le cas qui a motivé le correctif : Pierrelatte (26, vallée du Rhône) remontait en
    # tête d'une sélection alpine parce que le filtre ne pouvait pas la voir.
    ouest = annotate(_bien(commune="Pierrelatte", departement="26"))
    est = annotate(_bien(commune="Le Bourg-d'Oisans", departement="38"))
    assert est_a_lest_du_rhone(ouest.latitude, ouest.longitude) is False
    assert est_a_lest_du_rhone(est.latitude, est.longitude) is True


def test_commune_fusionnee_ne_produit_pas_de_faux_point():
    # « La Rochette » (73) est devenue Valgelon-La Rochette en 2019 ; les annonces
    # gardent l'ancien nom. On préfère l'absence de coordonnées à celles que la BAN
    # rendrait : elle place « La Rochette 73 » en Seine-et-Marne, à 400 km.
    assert coords_for_commune("La Rochette", "73") is None
    it = annotate(_bien(commune="La Rochette", departement="73"))
    assert it.latitude is None
    assert "position_commune" not in it.flags
