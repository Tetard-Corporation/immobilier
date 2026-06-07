"""Provider densité communale : signal d'isolement à partir de la population INSEE.

Une commune très peu peuplée et peu dense rend un bien plus probablement isolé,
même quand l'annonce ne le mentionne pas. On lit la population du référentiel des
communes (BAN renvoie le code INSEE ; la population vient de l'API Geo).
"""

from __future__ import annotations

from .base import EnrichmentProvider


def isolement_score(population: int | None) -> float | None:
    """Score d'isolement 0–1 à partir de la population communale (décroissant)."""
    if population is None:
        return None
    if population <= 200:
        return 1.0
    if population >= 10000:
        return 0.0
    # interpolation log-ish simple entre 200 et 10000 hab.
    import math

    return round(max(0.0, min(1.0, 1 - (math.log10(population) - math.log10(200)) / (math.log10(10000) - math.log10(200)))), 3)


class DensiteProvider(EnrichmentProvider):
    name = "densite"

    def _fetch(self, lat: float, lon: float) -> dict:
        code = self._reverse_citycode(lat, lon)
        if not code:
            return {}
        resp = self._get_client().get(
            "https://geo.api.gouv.fr/communes",
            params={"code": code, "fields": "population,surface", "format": "json"},
        )
        resp.raise_for_status()
        data = resp.json()
        rec = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
        if not rec:
            return {}
        pop = rec.get("population")
        out: dict = {}
        if isinstance(pop, int):
            out["population_commune"] = pop
            iso = isolement_score(pop)
            if iso is not None:
                out["isolement_score"] = iso
            surface = rec.get("surface")  # en hectares
            if isinstance(surface, (int, float)) and surface > 0:
                out["densite_hab_km2"] = round(pop / (surface / 100.0), 1)
        return out
