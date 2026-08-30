"""Exposition et durée d'ensoleillement d'un point (relief IGN + géométrie solaire).

Pourquoi ce module. En vallée alpine, deux maisons distantes d'un kilomètre ne passent pas
le même hiver : l'ubac reste des semaines à l'ombre du versant d'en face pendant que
l'adret, en face, est au soleil toute la journée. Rien dans l'annonce ne le dit — « plein
sud » y est un argument de vente, jamais une mesure — et l'altitude ne le dit pas non plus
(les deux versants ont la même).

Ce qu'on mesure, à partir du seul modèle d'altitude IGN :

- l'EXPOSITION — l'orientation du versant (azimut de la ligne de plus grande pente) et son
  inclinaison, lues sur une couronne courte de 90 m autour du point ;
- la DURÉE d'ensoleillement — les heures de soleil DIRECT au solstice d'hiver et à
  l'équinoxe, obtenues en confrontant la course du soleil au MASQUE d'horizon : l'angle
  sous lequel le relief environnant barre le ciel, échantillonné de l'est à l'ouest par le
  sud, jusqu'à 8 km.

C'est le masque qui décide, pas l'orientation. À 45° de latitude, le soleil du 21 décembre
culmine à 21,5° au-dessus de l'horizon : une crête qui domine le fond de vallée de 400 m à
1 km de distance en barre 22 — le fond de vallée ne voit alors pas le soleil de la journée,
quelle que soit l'orientation de sa pente. C'est pourquoi la durée pèse plus que
l'exposition dans le critère qui consomme ces mesures.

Les fonctions de calcul sont PURES (elles reçoivent des altitudes, pas une URL) : c'est ce
qui les rend testables sans réseau. L'échantillonnage IGN vit dans `export_static`, à côté
des autres mesures de relief, et passe par le même cache disque.
"""

from __future__ import annotations

import math

# Azimuts échantillonnés, en degrés depuis le nord (90 = est, 180 = sud, 270 = ouest).
# À 45° de latitude, le soleil du 21 décembre se lève au 127° et se couche au 233° : hors
# de cet arc, un point de masque de plus ne changerait aucune des heures mesurées.
AZIMUTS = tuple(range(90, 271, 15))
# Distances d'échantillonnage (m). La crête qui décide n'est pas toujours la plus proche :
# un talus à 250 m barre 10°, la montagne d'en face à 4 km en barre 25.
DISTANCES = (250, 500, 1000, 2000, 4000, 8000)
# Rayon de la couronne qui donne l'orientation du versant. Court volontairement : c'est la
# pente SOUS la maison qui l'expose, pas la forme générale de la vallée.
RAYON_PENTE = 90

DECLINAISON_HIVER = -23.44   # 21 décembre
DECLINAISON_EQUINOXE = 0.0   # 20 mars / 22 septembre
_RAYON_TERRE = 6_371_000
_PAS_MINUTES = 4             # 1° d'angle horaire par pas
_NO_DATA = -1000             # l'IGN renvoie -99999 hors zone / en mer

_ROSE = ("nord", "nord-est", "est", "sud-est", "sud", "sud-ouest", "ouest", "nord-ouest")


def _deplacer(lat: float, lon: float, azimut_deg: float, distance_m: float) -> tuple[float, float]:
    a = math.radians(azimut_deg)
    return (lat + distance_m * math.cos(a) / 111320,
            lon + distance_m * math.sin(a) / (111320 * math.cos(math.radians(lat))))


def points_a_mesurer(lat: float, lon: float) -> list[tuple[float, float]]:
    """Points dont il faut l'altitude, dans l'ordre attendu par `mesurer`.

    Le site, puis la couronne de pente (8 points), puis la grille azimut × distance.
    87 points au total, soit 4 requêtes IGN groupées — du même ordre que la mesure fine
    de proéminence, et une seule fois par point grâce au cache.
    """
    pts = [(lat, lon)]
    pts += [_deplacer(lat, lon, 45 * k, RAYON_PENTE) for k in range(8)]
    pts += [_deplacer(lat, lon, az, d) for az in AZIMUTS for d in DISTANCES]
    return pts


def _valide(z) -> bool:
    return isinstance(z, (int, float)) and z > _NO_DATA


def pente_et_exposition(site: float, couronne: list) -> tuple[float | None, float]:
    """(azimut de la plus grande pente, inclinaison en degrés) depuis la couronne.

    Ajustement d'un plan au moindre carré sur les points valides. Sous 0,5° le terrain est
    plat : l'azimut n'y veut plus rien dire (le bruit du modèle d'altitude le fait tourner
    d'un quadrant à l'autre), on renvoie donc None plutôt qu'une orientation inventée.
    """
    sx = sy = dx2 = dy2 = 0.0
    for k, z in enumerate(couronne):
        if not _valide(z):
            continue
        a = math.radians(45 * k)
        est, nord = RAYON_PENTE * math.sin(a), RAYON_PENTE * math.cos(a)
        dz = z - site
        sx += dz * est
        dx2 += est * est
        sy += dz * nord
        dy2 += nord * nord
    if not dx2 or not dy2:
        return None, 0.0
    gx, gy = sx / dx2, sy / dy2
    pente = math.degrees(math.atan(math.hypot(gx, gy)))
    if pente < 0.5:
        return None, pente
    # Le versant « regarde » dans le sens de la DESCENTE : l'opposé du gradient.
    return math.degrees(math.atan2(-gx, -gy)) % 360, pente


def rose(azimut: float | None) -> str:
    return "terrain plat" if azimut is None else _ROSE[int((azimut + 22.5) // 45) % 8]


def position_soleil(lat: float, declinaison: float, angle_horaire: float) -> tuple[float, float]:
    """(hauteur, azimut) du soleil, en degrés. Angle horaire : 0 au midi solaire, 15°/h."""
    phi, delta, H = math.radians(lat), math.radians(declinaison), math.radians(angle_horaire)
    sin_h = math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.cos(H)
    hauteur = math.degrees(math.asin(max(-1.0, min(1.0, sin_h))))
    azimut = (180 + math.degrees(math.atan2(
        math.sin(H), math.cos(H) * math.sin(phi) - math.tan(delta) * math.cos(phi)))) % 360
    return hauteur, azimut


def angle_masque(masque: dict[float, float], azimut: float) -> float:
    """Hauteur d'horizon (degrés) dans une direction, interpolée entre deux azimuts mesurés.

    Hors de l'arc échantillonné, on prolonge par la valeur du bord plutôt que par zéro :
    supposer l'horizon dégagé là où on n'a pas regardé ferait passer une combe pour un
    balcon.
    """
    if not masque:
        return 0.0
    azs = sorted(masque)
    if azimut <= azs[0]:
        return masque[azs[0]]
    if azimut >= azs[-1]:
        return masque[azs[-1]]
    for a, b in zip(azs, azs[1:]):
        if a <= azimut <= b:
            return masque[a] + (azimut - a) / (b - a) * (masque[b] - masque[a])
    return 0.0


def heures_de_soleil(lat: float, masque: dict[float, float],
                     declinaison: float = DECLINAISON_HIVER,
                     pas_minutes: int = _PAS_MINUTES) -> float:
    """Heures de soleil direct sur la journée : on balaie la course du soleil et on compte
    les pas où il est à la fois au-dessus de l'horizon et au-dessus du relief."""
    pas_deg = pas_minutes / 4.0  # 15°/h
    au_soleil, h = 0, -180.0 + pas_deg / 2
    while h < 180.0:
        hauteur, azimut = position_soleil(lat, declinaison, h)
        if hauteur > 0 and hauteur > angle_masque(masque, azimut):
            au_soleil += 1
        h += pas_deg
    return au_soleil * pas_minutes / 60.0


def mesurer(lat: float, altitudes: list) -> dict | None:
    """Flags d'ensoleillement depuis les altitudes de `points_a_mesurer`. None si le
    point lui-même n'a pas d'altitude (hors zone IGN) — on ne devine pas."""
    attendu = 1 + 8 + len(AZIMUTS) * len(DISTANCES)
    if not altitudes or len(altitudes) < attendu:
        return None
    site = altitudes[0]
    if not _valide(site):
        return None

    azimut_pente, pente = pente_et_exposition(site, altitudes[1:9])

    masque, i = {}, 9
    for az in AZIMUTS:
        angles = []
        for d in DISTANCES:
            z = altitudes[i]
            i += 1
            if not _valide(z):
                continue
            # Courbure terrestre : elle abaisse l'horizon de 5 m à 8 km. Négligeable
            # devant une crête, pas devant une plaine — et elle ne coûte qu'un terme.
            angles.append(math.degrees(math.atan2(z - site - d * d / (2 * _RAYON_TERRE), d)))
        masque[az] = max(angles) if angles else 0.0

    return {
        "soleil_hiver_h": round(heures_de_soleil(lat, masque, DECLINAISON_HIVER), 2),
        "soleil_equinoxe_h": round(heures_de_soleil(lat, masque, DECLINAISON_EQUINOXE), 2),
        "masque_sud_deg": round(masque.get(180, 0.0), 1),
        "exposition_deg": None if azimut_pente is None else round(azimut_pente),
        "exposition": rose(azimut_pente),
        "pente_deg": round(pente, 1),
        "soleil_checked": True,
    }


def formater_heures(h: float) -> str:
    """4.3 -> « 4h18 »."""
    heures, minutes = divmod(int(round(h * 60)), 60)
    return f"{heures}h{minutes:02d}"
