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

    def _check_blocked(self, resp: httpx.Response) -> None:
        if resp.status_code in (403, 429) or self._looks_blocked(resp.text):
            raise ScraperBlocked(
                f"{self.label} a renvoyé un blocage (HTTP {resp.status_code}). "
                "Configurer PROXY_URL et/ou activer le mode headless."
            )

    def _get(self, path: str, *, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
        cache_key = f"GET {path}?{sorted((params or {}).items())}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < self._settings.cache_ttl_seconds:
            return cached[1]  # type: ignore[return-value]

        self._respect_rate_limit()
        resp = self._get_client().get(path, params=params, headers=headers)
        self._check_blocked(resp)
        resp.raise_for_status()
        self._cache[cache_key] = (now, resp)
        return resp

    def _post(self, path: str, *, json_body: dict, headers: dict | None = None) -> httpx.Response:
        """POST JSON (API internes type Leboncoin). Mêmes garde-fous anti-blocage."""
        import json as _json

        cache_key = f"POST {path} {_json.dumps(json_body, sort_keys=True)}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < self._settings.cache_ttl_seconds:
            return cached[1]  # type: ignore[return-value]

        self._respect_rate_limit()
        resp = self._get_client().post(path, json=json_body, headers=headers)
        self._check_blocked(resp)
        resp.raise_for_status()
        self._cache[cache_key] = (now, resp)
        return resp

    def _fetch_headless(self, url: str, *, wait_selector: str | None = None) -> str:
        """Récupère le HTML via un navigateur headless (Playwright) + proxy éventuel.

        Playwright est une dépendance optionnelle : `pip install playwright &&
        playwright install chromium`.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - dépend de l'environnement
            raise ScraperBlocked(
                "Mode headless requis mais Playwright absent : "
                "`pip install playwright && playwright install chromium`."
            ) from exc

        proxy = {"server": self._settings.proxy_url} if self._settings.proxy_url else None
        self._respect_rate_limit()
        with sync_playwright() as p:  # pragma: no cover - nécessite un navigateur
            browser = p.chromium.launch(headless=True, proxy=proxy)
            try:
                page = browser.new_page(user_agent=_DEFAULT_HEADERS["User-Agent"])
                page.goto(url, wait_until="domcontentloaded", timeout=self._settings.http_timeout_seconds * 1000)
                if wait_selector:
                    page.wait_for_selector(wait_selector, timeout=self._settings.http_timeout_seconds * 1000)
                content = page.content()
            finally:
                browser.close()
        if self._looks_blocked(content):
            raise ScraperBlocked(f"{self.label} : page de blocage même en headless (proxy requis).")
        return content
