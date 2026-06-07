"""Provider Géorisques : risques naturels/technologiques d'un point (sans clé)."""

from __future__ import annotations

from .base import EnrichmentProvider


class GeorisquesProvider(EnrichmentProvider):
    name = "georisques"

    def _fetch(self, lat: float, lon: float) -> dict:
        resp = self._get_client().get(
            f"{self._settings.georisques_api_url}/resultats_rapport_risque",
            params={"latlon": f"{lon},{lat}"},
        )
        resp.raise_for_status()
        data = resp.json()
        risques: list[str] = []
        for famille in ("risquesNaturels", "risquesTechnologiques"):
            bloc = data.get(famille) or {}
            if isinstance(bloc, dict):
                for nom, val in bloc.items():
                    present = val.get("present") if isinstance(val, dict) else val
                    if present:
                        risques.append(nom)
        return {"risques": risques}
