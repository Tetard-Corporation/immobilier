"""Provider 'proximité gare' : gare la plus proche à partir d'un jeu open data.

Un sous-ensemble des gares principales est embarqué (`data/gares.csv`). Pour une
couverture complète, remplacer le fichier par l'export open data SNCF
(« referentiel-gares-voyageurs ») au même format `nom,lat,lon`.
"""

from __future__ import annotations

import csv
import functools
import os

from .geo import haversine_km

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "gares.csv")


@functools.lru_cache(maxsize=1)
def _load(path: str) -> list[tuple[str, float, float]]:
    gares: list[tuple[str, float, float]] = []
    if not os.path.exists(path):
        return gares
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                gares.append((row["nom"], float(row["lat"]), float(row["lon"])))
            except (KeyError, ValueError):
                continue
    return gares


def nearest_gare(lat: float, lon: float, path: str | None = None) -> tuple[str, float] | None:
    """Renvoie (nom, distance_km) de la gare la plus proche, ou None."""
    gares = _load(path or _DEFAULT_PATH)
    if not gares or lat is None or lon is None:
        return None
    best = min(gares, key=lambda g: haversine_km(lat, lon, g[1], g[2]))
    return best[0], round(haversine_km(lat, lon, best[1], best[2]), 1)
