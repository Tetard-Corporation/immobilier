"""Provider temps de trajet train via Navitia (clé requise : NAVITIA_API_KEY).

Calcule la durée du meilleur trajet en transport en commun depuis une ville d'origine
(par défaut Paris) jusqu'au bien. Tant qu'aucune clé n'est configurée, le provider est
indisponible et la préférence `rail_time_from` reste en `pending`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx

from ..services.geo import resolve_city
from .base import EnrichmentProvider


class RailTimeProvider(EnrichmentProvider):
    name = "rail_time"

    @property
    def available(self) -> bool:
        return self._settings.navitia_configured and resolve_city(self._settings.navitia_origin) is not None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._settings.http_timeout_seconds,
                auth=(self._settings.navitia_api_key, ""),
            )
        return self._client

    @staticmethod
    def _parse_minutes(payload: dict) -> int | None:
        journeys = [j for j in (payload.get("journeys") or []) if isinstance(j, dict)]
        durations = [j["duration"] for j in journeys if isinstance(j.get("duration"), (int, float))]
        return round(min(durations) / 60) if durations else None

    def _fetch(self, lat: float, lon: float) -> dict:
        origin = resolve_city(self._settings.navitia_origin)
        if origin is None:
            return {}
        when = (datetime.now() + timedelta(days=1)).strftime("%Y%m%dT080000")
        resp = self._get_client().get(
            f"{self._settings.navitia_url}/journeys",
            params={
                "from": f"{origin[1]};{origin[0]}",
                "to": f"{lon};{lat}",
                "datetime": when,
                "count": 3,
            },
        )
        resp.raise_for_status()
        minutes = self._parse_minutes(resp.json())
        return {"rail_time_min": minutes} if minutes is not None else {}
