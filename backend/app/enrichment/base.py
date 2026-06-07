"""Base des providers d'enrichissement (données officielles géolocalisées).

Un provider prend des coordonnées (lat, lon) et renvoie un dict de champs fusionnés
dans `listing.flags` (ex. {"altitude": 167}, {"risques": [...]}, {"constructible": True}).
Les résultats sont mis en cache par coordonnées arrondies pour limiter les appels.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

import httpx

from ..config import Settings, get_settings


class EnrichmentProvider(ABC):
    name = "base"

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._cache: dict[tuple, tuple[float, dict]] = {}

    @property
    def available(self) -> bool:
        return True

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._settings.http_timeout_seconds,
                headers={"User-Agent": "ImmobilierBot/0.1"},
                follow_redirects=True,
            )
        return self._client

    def enrich(self, lat: float, lon: float) -> dict:
        """Version cachée par coordonnées arrondies (~11 m)."""
        if lat is None or lon is None:
            return {}
        key = (round(lat, 4), round(lon, 4))
        now = time.time()
        hit = self._cache.get(key)
        if hit and now - hit[0] < self._settings.cache_ttl_seconds:
            return hit[1]
        try:
            value = self._fetch(lat, lon)
        except Exception:
            value = {}
        self._cache[key] = (now, value)
        return value

    def _reverse_citycode(self, lat: float, lon: float) -> str | None:
        """Résout le code INSEE de la commune (reverse-geocoding BAN)."""
        resp = self._get_client().get(self._settings.ban_reverse_url, params={"lat": lat, "lon": lon})
        resp.raise_for_status()
        feats = resp.json().get("features") or []
        return feats[0]["properties"].get("citycode") if feats else None

    @abstractmethod
    def _fetch(self, lat: float, lon: float) -> dict:
        """Appel réseau + parsing. Doit renvoyer un dict (vide si rien)."""
