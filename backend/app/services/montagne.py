"""Être À LA montagne, ce qui n'est pas la même chose qu'être EN altitude.

Le critère `relief_mountain` notait l'altitude du bien, rapportée à une référence
(800 m). Il se trompait des deux côtés, et le groupe l'a dit : « montagne / relief ne
devrait pas compter l'altitude du bien mais la proximité à des montagnes ».

- Un village de fond de vallée à 300 m, cerné de sommets à 2 000 m, est à la montagne.
  Le critère lui donnait 0,38.
- Un plateau nu à 900 m, sans rien au-dessus à vingt kilomètres à la ronde, ne l'est pas.
  Le critère lui donnait 1,00.

Ce qu'on mesure à la place, sur le seul modèle d'altitude IGN : le RELIEF ALENTOUR.
Quarante points sur cinq couronnes (3, 6, 10, 15 et 20 km) et huit directions donnent
l'altitude du plus haut sommet des environs, et l'amplitude entre ce sommet et le bien.

Les deux comptent, et pas pour la même raison. L'altitude du sommet dit de quelle
montagne il s'agit — 1 200 m dans le Jura, 2 500 m en Oisans. L'amplitude dit ce qu'on a
sous les yeux : 1 500 m de dénivelé dans le champ de vision, c'est un paysage de
montagne, quelle que soit l'altitude où l'on se tient.

Les fonctions sont PURES (elles reçoivent des altitudes, pas une URL), comme celles de
`soleil.py` : c'est ce qui les rend testables sans réseau. L'échantillonnage IGN vit dans
`export_static`, à côté des autres mesures de relief, et passe par un cache disque.
"""

from __future__ import annotations

import math

# Couronnes d'échantillonnage (m). Vingt kilomètres est la distance à laquelle un massif
# cesse de faire partie du lieu où l'on vit : au-delà, on le voit sans y être.
RAYONS = (3000, 6000, 10000, 15000, 20000)
AZIMUTS = tuple(range(0, 360, 45))

# Repères du barème. 2 200 m est l'ordre de grandeur d'un sommet des Alpes du Nord vu
# depuis ses vallées (Belledonne, Vercors oriental, Bauges hautes) ; 600 m celui d'une
# colline. 1 200 m d'amplitude, c'est le dénivelé d'une vallée alpine.
_SOMMET_COLLINE = 600
_SOMMET_ALPIN = 2200
_AMPLITUDE_PLEINE = 1200
# La hauteur du sommet pèse un peu plus que l'amplitude : à amplitude égale, être au pied
# d'un 2 000 et être au pied d'un 1 000 ne se ressemblent pas.
_POIDS_SOMMET = 0.6

_NO_DATA = -1000   # l'IGN renvoie -99999 hors zone / en mer


def _deplacer(lat: float, lon: float, azimut_deg: float, distance_m: float) -> tuple[float, float]:
    a = math.radians(azimut_deg)
    return (lat + distance_m * math.cos(a) / 111320,
            lon + distance_m * math.sin(a) / (111320 * math.cos(math.radians(lat))))


def points_a_mesurer(lat: float, lon: float) -> list[tuple[float, float]]:
    """Le site, puis la grille couronne × azimut. 41 points, soit 2 requêtes groupées."""
    return [(lat, lon)] + [_deplacer(lat, lon, az, r) for r in RAYONS for az in AZIMUTS]


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def noter(alt_site: float | None, alt_max: float | None,
          ref_sommet: float | None = None) -> float | None:
    """Note de montagne, entre 0 et 1, depuis l'altitude du site et celle du plus haut point.

    `ref_sommet` : l'altitude de sommet qui vaut la note pleine. C'est le réglage
    personnel du critère — qui trouve qu'un 1 500 m suffit le descend, qui ne compte que
    la haute montagne le monte. Il remplace l'ancien `ref_altitude`, qui réglait une
    mesure devenue fausse (l'altitude du bien lui-même).
    """
    if alt_site is None or alt_max is None:
        return None
    haut = float(ref_sommet or _SOMMET_ALPIN)
    if haut <= _SOMMET_COLLINE:
        haut = _SOMMET_COLLINE + 1
    sommet = _clamp((alt_max - _SOMMET_COLLINE) / (haut - _SOMMET_COLLINE))
    amplitude = _clamp((alt_max - alt_site) / _AMPLITUDE_PLEINE)
    return round(_POIDS_SOMMET * sommet + (1 - _POIDS_SOMMET) * amplitude, 3)


def mesurer(altitudes: list) -> dict | None:
    """Drapeaux de montagne depuis les altitudes de `points_a_mesurer`, dans l'ordre.

    None si le site lui-même n'a pas d'altitude : sans lui, l'amplitude n'existe pas.
    Les points hors zone (mer, frontière) sont simplement ignorés — un massif mesuré sur
    trente points au lieu de quarante reste mesuré.
    """
    valides = [z for z in altitudes if isinstance(z, (int, float)) and z > _NO_DATA]
    if not altitudes or not valides:
        return None
    site = altitudes[0]
    if not (isinstance(site, (int, float)) and site > _NO_DATA):
        return None
    alt_max = max(valides)
    return {
        "alt_site_m": round(site),
        "alt_max_20km_m": round(alt_max),
        "amplitude_m": round(alt_max - site),
        "montagne_note": noter(site, alt_max),
        "montagne_checked": True,
    }
