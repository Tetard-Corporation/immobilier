"""Couche headless réutilisable : récolte de cookies anti-bot (Datadome).

Les portails « durs » (Leboncoin, SeLoger) sont protégés par Datadome, qui bloque
les IP datacenter et exige un cookie valide, lié au User-Agent et à l'IP qui l'ont
obtenu. La stratégie retenue : sur le poste de dev (IP résidentielle), un navigateur
Chromium **persistant** charge le site une fois — l'utilisateur résout l'éventuel
captcha à la main (mode « headed ») — puis on extrait le cookie `datadome` et on le
met en cache sur disque. Les exécutions suivantes (API HTTP, même headless) réutilisent
ce cookie tant qu'il est frais.

Playwright est une dépendance optionnelle (`requirements-scrapers.txt`). L'import est
différé pour que ce module reste importable sans navigateur (le cache disque, lui,
fonctionne toujours).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import Settings, get_settings

# UA d'un Chrome desktop récent. Datadome lie le cookie au User-Agent : on récolte
# avec cet UA et on réémet les requêtes API avec le MÊME UA (cf. LeboncoinSource).
DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_BLOCK_MARKERS = ("captcha-delivery", "geo.captcha-delivery", "datadome", "just a moment")


def _cache_path(settings: Settings) -> Path:
    return Path(settings.headless_state_dir) / "datadome.json"


def _profile_dir(settings: Settings, domain: str) -> Path:
    # Un profil Chromium persistant par domaine : conserve le solve Datadome.
    safe = domain.replace(".", "_")
    return Path(settings.headless_state_dir) / "profiles" / safe


def _read_cache(settings: Settings) -> dict:
    path = _cache_path(settings)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except (ValueError, OSError):
        return {}


def get_cached_datadome(domain: str, *, settings: Settings | None = None) -> dict | None:
    """Renvoie {"cookie", "ua", "ts"} en cache pour `domain`, ou None si absent/périmé."""
    settings = settings or get_settings()
    entry = _read_cache(settings).get(domain)
    if not isinstance(entry, dict) or not entry.get("cookie"):
        return None
    max_age = settings.datadome_max_age_minutes * 60
    if max_age > 0 and (time.time() - float(entry.get("ts", 0))) > max_age:
        return None
    return entry


def store_datadome(domain: str, cookie: str, ua: str, *, settings: Settings | None = None) -> None:
    """Persiste le cookie récolté (atomique, par domaine)."""
    settings = settings or get_settings()
    path = _cache_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_cache(settings)
    data[domain] = {"cookie": cookie, "ua": ua, "ts": time.time()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), "utf-8")
    tmp.replace(path)


def harvest_datadome(
    url: str,
    domain: str,
    *,
    settings: Settings | None = None,
    headed: bool | None = None,
    timeout_seconds: int | None = None,
) -> dict | None:
    """Ouvre un navigateur persistant sur `url`, attend le cookie `datadome`, le met en cache.

    - `headed` : navigateur visible (défaut : `settings.headless_headed`). En visible,
      laisse le temps de résoudre un captcha manuellement.
    - `timeout_seconds` : durée max d'attente du cookie (généreux en headed).

    Renvoie l'entrée de cache {"cookie", "ua", "ts"} ou None si rien n'a été obtenu.
    """
    settings = settings or get_settings()
    headed = settings.headless_headed if headed is None else headed
    # Headed : on laisse le temps d'un captcha manuel ; headless : court.
    timeout_seconds = timeout_seconds if timeout_seconds is not None else (240 if headed else 45)

    try:  # pragma: no cover - dépend de l'environnement
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Mode headless requis mais Playwright absent : "
            "`pip install -r requirements-scrapers.txt && playwright install chromium`."
        ) from exc

    profile = _profile_dir(settings, domain)
    profile.mkdir(parents=True, exist_ok=True)
    proxy = {"server": settings.proxy_url} if settings.proxy_url else None
    deadline = time.time() + timeout_seconds

    with sync_playwright() as p:  # pragma: no cover - nécessite un navigateur
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=not headed,
            proxy=proxy,
            locale="fr-FR",
            timezone_id="Europe/Paris",
            viewport={"width": 1366, "height": 900},
            user_agent=DESKTOP_UA,
            args=["--disable-blink-features=AutomationControlled"],
        )
        # Anti-fingerprint minimal : masque navigator.webdriver.
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=settings.http_timeout_seconds * 1000)
            cookie = _poll_cookie(ctx, page, domain, deadline)
        finally:
            ctx.close()

    if not cookie:
        return None
    store_datadome(domain, cookie, DESKTOP_UA, settings=settings)
    return {"cookie": cookie, "ua": DESKTOP_UA, "ts": time.time()}


def _poll_cookie(ctx, page, domain: str, deadline: float) -> str | None:  # pragma: no cover
    """Attend l'apparition d'un cookie `datadome` non vide (le temps d'un éventuel captcha)."""
    while time.time() < deadline:
        for c in ctx.cookies():
            if c.get("name") == "datadome" and c.get("value") and domain in (c.get("domain") or ""):
                # Un cookie posé avant résolution du captcha reste sur la page de blocage :
                # on s'assure qu'on n'est plus sur une page Datadome.
                try:
                    html = page.content()[:2000].lower()
                except Exception:
                    html = ""
                if not any(m in html for m in _BLOCK_MARKERS):
                    return c["value"]
        time.sleep(1.0)
    # Dernier recours : renvoyer le cookie même si la détection de page a échoué.
    for c in ctx.cookies():
        if c.get("name") == "datadome" and c.get("value"):
            return c["value"]
    return None
