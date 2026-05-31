"""Base commune aux connecteurs par scraping (HTTP léger).

Fournit : client httpx configuré (proxy, en-têtes réalistes), limitation de débit,
back-off, cache mémoire à TTL et détection de blocage (Cloudflare/Datadome).
Les connecteurs « durs » (Leboncoin, SeLoger) ajouteront un mode headless par-dessus.
"""

from __future__ import annotations

import threading
import time

import httpx

from ..config import Settings, get_settings
from .base import ListingSource

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

# Marqueurs typiques d'une page de blocage anti-bot.
_BLOCK_MARKERS = ("just a moment", "captcha-delivery", "datadome", "cf-chl", "access denied")


class ScraperBlocked(RuntimeError):
    """Levée quand la source répond par une page anti-bot."""


class ScraperSource(ListingSource):
    """Source basée sur des requêtes HTTP. Sous-classes : implémentent search/get."""

    name = "scraper"
    label = "Scraper"
    base_url: str = ""

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    @property
    def available(self) -> bool:
        # Le scraping ne requiert pas de clé. La disponibilité réelle dépend du réseau.
        return True

    # -- HTTP ---------------------------------------------------------------- #
    def _get_client(self) -> httpx.Client:
        if self._client is None:
            proxy = self._settings.proxy_url or None
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=_DEFAULT_HEADERS,
                timeout=self._settings.http_timeout_seconds,
                follow_redirects=True,
                proxy=proxy,
            )
        return self._client

    def _respect_rate_limit(self) -> None:
        min_interval = self._settings.scraper_rate_limit_ms / 1000.0
        with self._lock:
            elapsed = time.time() - self._last_request_at
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            self._last_request_at = time.time()

    @staticmethod
    def _looks_blocked(text: str) -> bool:
        head = text[:2000].lower()
        return any(marker in head for marker in _BLOCK_MARKERS)

    def _get(self, path: str, *, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
        cache_key = f"{path}?{sorted((params or {}).items())}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < self._settings.cache_ttl_seconds:
            return cached[1]  # type: ignore[return-value]

        self._respect_rate_limit()
        resp = self._get_client().get(path, params=params, headers=headers)
        if resp.status_code in (403, 429) or self._looks_blocked(resp.text):
            raise ScraperBlocked(
                f"{self.label} a renvoyé un blocage (HTTP {resp.status_code}). "
                "Configurer PROXY_URL ou activer le mode headless."
            )
        resp.raise_for_status()
        self._cache[cache_key] = (now, resp)
        return resp
