"""Provider relief : altitude d'un point via l'API altimétrie IGN (sans clé)."""

from __future__ import annotations

from .base import EnrichmentProvider


class ReliefProvider(EnrichmentProvider):
    name = "relief"

    def _fetch(self, lat: float, lon: float) -> dict:
        resp = self._get_client().get(
            self._settings.ign_alti_url,
            params={"lon": lon, "lat": lat, "resource": "ign_rge_alti_wld", "zonly": "true"},
        )
        resp.raise_for_status()
        elevations = resp.json().get("elevations") or []
        if not elevations:
            return {}
        alt = elevations[0]
        if not isinstance(alt, (int, float)) or alt < -100:
            return {}
        return {"altitude": round(float(alt), 1), "montagne": alt >= 600}
