"""Provider socio-politique : profil de la commune (âge médian, orientation à gauche).

Lit un jeu de données local (`data/communes_socio.csv`, clé = code INSEE) — à alimenter
avec l'âge médian INSEE et la part de gauche aux élections (Ministère de l'Intérieur).
Produit des scores 0–1 exploités comme **préférences** (`population_jeune`,
`orientation_gauche`) dans le classement. Commune absente du jeu de données => `pending`.
"""

from __future__ import annotations

import csv
import functools
import os

from .base import EnrichmentProvider

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "communes_socio.csv"
)


@functools.lru_cache(maxsize=1)
def _load(path: str) -> dict[str, tuple[float, float]]:
    data: dict[str, tuple[float, float]] = {}
    if not os.path.exists(path):
        return data
    with open(path, encoding="utf-8") as fh:
        rows = (line for line in fh if not line.lstrip().startswith("#"))
        for row in csv.DictReader(rows):
            try:
                data[row["code_insee"].strip()] = (float(row["age_median"]), float(row["part_gauche"]))
            except (KeyError, ValueError, AttributeError):
                continue
    return data


def _normalize_commune(code: str) -> str:
    """Ramène les arrondissements (Paris/Lyon/Marseille) au code commune global."""
    if code.startswith("751"):
        return "75056"
    if code.startswith("6938"):
        return "69123"
    if code.startswith("132"):
        return "13055"
    return code


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def socio_scores(age_median: float, part_gauche: float) -> dict:
    return {
        "age_median": age_median,
        "part_gauche": round(part_gauche, 3),
        "pop_jeune_score": round(_clamp(1 - (age_median - 30) / 25), 3),
        "orientation_gauche_score": round(_clamp(part_gauche), 3),
    }


class SocioProvider(EnrichmentProvider):
    name = "socio"

    def _path(self) -> str:
        p = self._settings.socio_dataset_path
        return p if os.path.isabs(p) else (_DEFAULT_PATH if p == "data/communes_socio.csv" else p)

    def _fetch(self, lat: float, lon: float) -> dict:
        code = self._reverse_citycode(lat, lon)
        if not code:
            return {}
        row = _load(self._path()).get(_normalize_commune(code))
        if row is None:
            return {}
        return socio_scores(row[0], row[1])
