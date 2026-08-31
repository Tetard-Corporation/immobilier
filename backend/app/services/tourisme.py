"""Attractivité locative saisonnière d'un point : ce qu'un logement peut se louer ici.

Le critère demandé s'appelle « attractivité Airbnb », et il ne se lit pas dans l'annonce :
une annonce dit « idéal investissement locatif » de la même façon qu'elle dit « plein
sud », c'est-à-dire sans l'avoir vérifié. Ce qui se mesure, ce sont les quatre choses
qui font qu'un logement se loue à la semaine — et elles sont toutes dans OpenStreetMap :

| Ce qu'on mesure | Rayon | Ce que ça dit |
|---|---|---|
| remontée mécanique la plus proche | 25 km | la saison d'hiver, la plus rentable des deux |
| lac le plus proche, sites touristiques | 12 / 10 km | la saison d'été, celle qui remplit juillet-août |
| hôtels, gîtes, chalets, campings | 5 km | un marché locatif EXISTE déjà ici |
| restaurants, cafés, bars | 3 km | ce dont un locataire a besoin sur place |

Le calcul est ici, pur et hors ligne (comme `soleil.py`) ; l'échantillonnage Overpass et
son cache vivent dans `export_static.py`, réchauffés par `scripts/warm_tourisme.py`.

**La densité d'hébergements mesure l'offre, pas la demande** — c'est le compromis assumé
du critère. Aucune source ouverte ne donne le taux d'occupation ou le prix à la nuitée
d'une commune ; l'hébergement touristique déjà installé est le meilleur substitut
disponible, parce qu'il s'est installé là où on loue. Il a un angle mort : une commune
qui interdit ou plafonne la location de courte durée le mesurera pareil.
"""

from __future__ import annotations


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _palier(valeur: float | None, points: list[tuple[float, float]]) -> float:
    """Interpolation linéaire sur une liste de (seuil, note), seuils croissants."""
    if valeur is None:
        return 0.0
    if valeur <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if valeur <= x1:
            return y0 + (y1 - y0) * (valeur - x0) / (x1 - x0)
    return points[-1][1]


# Distance à la remontée mécanique. La pente est raide de bon droit : à 2 km on est
# « au pied des pistes » et le logement se loue à la semaine tout l'hiver ; à 10 km c'est
# une demi-heure de voiture matin et soir, ce que le locataire paie beaucoup moins ; à
# 25 km ce n'est plus une location de ski.
_SKI = [(2000, 1.0), (5000, 0.85), (10000, 0.55), (18000, 0.25), (25000, 0.0)]
# Distance au lac. Même logique, sur l'autre saison : au bord (1,5 km, à pied ou à vélo)
# contre « on prend la voiture pour aller à la plage » (8 km) contre rien (12 km).
_LAC = [(1500, 1.0), (4000, 0.7), (8000, 0.35), (12000, 0.0)]
# Saturations : au-delà, un site de plus ne change rien à la louabilité du logement.
_SAT_HEBERGEMENTS = 30   # hôtels/gîtes/chalets/campings à 5 km
_SAT_RESTOS = 15         # restaurants/cafés/bars à 3 km
_SAT_ATTRACTIONS = 60    # sites, points de vue, musées à 10 km

_POIDS = {"hiver": 0.34, "ete": 0.26, "marche": 0.25, "vie": 0.15}
# Prime de double saison. Un logement qui se loue l'hiver ET l'été n'a pas le même
# rendement qu'un logement qui se loue quatre mois : c'est le rapport de 1 à 2 entre une
# station de ski sans été et une vallée qui a les deux. Prime, pas critère à part, parce
# qu'elle ne récompense que le CROISEMENT des deux — d'où le min.
_PRIME_DEUX_SAISONS = 0.10


def noter(mesures: dict | None) -> dict | None:
    """Note d'attractivité locative [0,1] + le détail de ce qui la porte.

    `mesures` : ce que renvoie l'échantillonnage Overpass. None (ou vide) si le point
    n'a jamais été mesuré — le critère reste alors `pending`, il ne vaut pas zéro.
    """
    if not mesures or not mesures.get("tour_checked"):
        return None

    hiver = _palier(mesures.get("tour_dist_remontee_m"), _SKI)
    lac = _palier(mesures.get("tour_dist_lac_m"), _LAC)
    attractions = _clamp((mesures.get("tour_attractions") or 0) / _SAT_ATTRACTIONS)
    # L'été se gagne par le lac OU par les sites : Bourg-d'Oisans n'a pas de plage et
    # remplit pourtant juillet. Le second signal ajoute un peu au premier sans le
    # doubler — avoir les deux vaut mieux qu'avoir l'un, pas deux fois plus.
    ete = _clamp(max(lac, attractions) + 0.15 * min(lac, attractions))
    marche = _clamp((mesures.get("tour_hebergements") or 0) / _SAT_HEBERGEMENTS)
    vie = _clamp((mesures.get("tour_restos") or 0) / _SAT_RESTOS)

    note = (_POIDS["hiver"] * hiver + _POIDS["ete"] * ete
            + _POIDS["marche"] * marche + _POIDS["vie"] * vie
            + _PRIME_DEUX_SAISONS * min(hiver, ete))

    return {
        "note": round(_clamp(note), 3),
        "hiver": round(hiver, 2), "ete": round(ete, 2),
        "marche": round(marche, 2), "vie": round(vie, 2),
    }


def _km(m: float | None) -> str:
    return "—" if m is None else f"{m / 1000:.1f} km"


def resumer(mesures: dict, note: dict) -> str:
    """Phrase de détail affichée sous le critère, dans l'ordre de ce qui décide."""
    bouts = [f"ski à {_km(mesures.get('tour_dist_remontee_m'))}"]
    if mesures.get("tour_dist_lac_m") is not None:
        bouts.append(f"lac à {_km(mesures.get('tour_dist_lac_m'))}")
    bouts.append(f"{mesures.get('tour_hebergements') or 0} hébergements touristiques (5 km)")
    bouts.append(f"{mesures.get('tour_restos') or 0} restaurants/cafés (3 km)")
    bouts.append(f"{mesures.get('tour_attractions') or 0} sites (10 km)")
    saison = ("quatre saisons" if min(note["hiver"], note["ete"]) >= 0.5
              else "surtout l'hiver" if note["hiver"] > note["ete"] + 0.25
              else "surtout l'été" if note["ete"] > note["hiver"] + 0.25
              else "saison courte")
    return " · ".join(bouts) + f" — {saison}"
