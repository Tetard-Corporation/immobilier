"""Provider randonnées : densité de sentiers/itinéraires de rando (OSM/Overpass)."""

from __future__ import annotations

from .base import EnrichmentProvider

_OVERPASS = "https://overpass-api.de/api/interpreter"


class HikingProvider(EnrichmentProvider):
    name = "hiking"

    def _fetch(self, lat: float, lon: float) -> dict:
        q = (f'[out:json][timeout:25];('
             f'way(around:1500,{lat},{lon})[highway~"path|footway|bridleway"];'
             f'relation(around:3000,{lat},{lon})[route=hiking];);out count;')
        resp = self._get_client().post(_OVERPASS, data={"data": q})
        resp.raise_for_status()
        total = 0
        for el in resp.json().get("elements", []):
            try:
                total = max(total, int((el.get("tags") or {}).get("total", 0)))
            except (TypeError, ValueError):
                continue
        return {"randonnee": total >= 3, "rando_count": total}
