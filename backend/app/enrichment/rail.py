"""Provider temps de trajet train depuis une ville d'origine (par défaut Paris).

Deux modes, transparents pour le reste du moteur :
- **Sans clé (par défaut)** : estimation à vol d'oiseau (distance / vitesse moyenne
  ferroviaire + accès), suffisante pour *classer* les biens par éloignement.
- **Avec clé Navitia/SNCF** (`NAVITIA_API_KEY`, `NAVITIA_URL` pointant éventuellement
  vers api.sncf.com) : durée réelle du meilleur trajet.

Navitia n'offrant plus d'inscription libre gratuite, le mode estimation garantit que
le critère fonctionne pour tout le monde, sans inscription.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx

from ..services.geo import haversine_km, resolve_city
from .base import EnrichmentProvider

# Vitesse moyenne effective (km/h) et temps d'accès/attente (min) pour l'estimation.
_VITESSE_KMH = 150
_ACCES_MIN = 30


class RailTimeProvider(EnrichmentProvider):
    name = "rail_time"

    @property
    def available(self) -> bool:
        # Disponible dès qu'on sait géolocaliser la ville d'origine (clé non requise).
        return resolve_city(self._settings.navitia_origin) is not None

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

    @staticmethod
    def _approx_minutes(origin: tuple[float, float], lat: float, lon: float) -> int:
        dist = haversine_km(origin[0], origin[1], lat, lon)
        return round(_ACCES_MIN + dist / _VITESSE_KMH * 60)

    def _real_minutes(self, origin: tuple[float, float], lat: float, lon: float) -> int | None:
        when = (datetime.now() + timedelta(days=1)).strftime("%Y%m%dT080000")
        resp = self._get_client().get(
            f"{self._settings.navitia_url}/journeys",
            params={"from": f"{origin[1]};{origin[0]}", "to": f"{lon};{lat}", "datetime": when, "count": 3},
        )
        resp.raise_for_status()
        return self._parse_minutes(resp.json())

    def _fetch(self, lat: float, lon: float) -> dict:
        origin = resolve_city(self._settings.navitia_origin)
        if origin is None:
            return {}
        if self._settings.navitia_configured:
            try:
                minutes = self._real_minutes(origin, lat, lon)
                if minutes is not None:
                    return {"rail_time_min": minutes, "rail_time_estime": False}
            except Exception:
                pass  # repli sur l'estimation
        return {"rail_time_min": self._approx_minutes(origin, lat, lon), "rail_time_estime": True}
