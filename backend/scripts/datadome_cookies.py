"""Génère les cookies Datadome (leboncoin, seloger) depuis un VRAI navigateur headed.

À exécuter EN LOCAL uniquement (cf. docs/OPERATIONS.md §1) : le cookie est lié à
l'IP de sortie et à l'empreinte navigateur, il doit donc être généré ET utilisé
depuis la même machine résidentielle.

Le script pilote le Chrome installé (canal `chrome`, pas le Chromium Playwright :
Datadome reconnaît ce dernier), avec un profil persistant pour ne pas repasser le
challenge à chaque exécution. Si un challenge humain apparaît, le script attend
que l'utilisateur le résolve dans la fenêtre.

Sortie : écrit/actualise `backend/.env` (gitignoré) avec LEBONCOIN_DATADOME,
SELOGER_DATADOME et SCRAPER_USER_AGENT (l'UA réel du navigateur : Datadome
recoupe cookie et UA).

Usage :
    python scripts/datadome_cookies.py                 # les deux sites
    python scripts/datadome_cookies.py --site leboncoin
    python scripts/datadome_cookies.py --headless      # tentative sans fenêtre (déconseillé)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
PROFILE_DIR = os.path.join(BACKEND, ".pw-chrome-profile")
ENV_PATH = os.path.join(BACKEND, ".env")

SITES = {
    "leboncoin": {
        # Accueil d'abord : c'est là que Datadome pose le cookie. Une page de
        # listing demandée « à froid » (sans cookie) peut renvoyer un 403 sec.
        "urls": ["https://www.leboncoin.fr/", "https://www.leboncoin.fr/c/ventes_immobilieres"],
        "env": "LEBONCOIN_DATADOME",
        # Didomi (leboncoin) : bouton d'acceptation du consentement.
        "consent": ["#didomi-notice-agree-button", "button:has-text('Accepter & Fermer')"],
    },
    "seloger": {
        # /list.htm est mort (404 depuis la refonte SERP) : la page de résultats est
        # désormais /classified-search (cf. app/sources/seloger.py).
        "urls": ["https://www.seloger.com/",
                 "https://www.seloger.com/classified-search?distributionTypes=Buy&locations=AD08FR22130"],
        "env": "SELOGER_DATADOME",
        "consent": ["#didomi-notice-agree-button", "button:has-text('Accepter')",
                    "button:has-text('Tout accepter')"],
    },
}


def _cookie(ctx, name: str, host_substr: str) -> str | None:
    for c in ctx.cookies():
        if c.get("name") == name and host_substr in (c.get("domain") or ""):
            return c.get("value")
    return None


def harvest(site: str, ctx, wait_s: int) -> str | None:
    cfg = SITES[site]
    page = ctx.new_page()
    print(f"\n=== {site}", flush=True)
    for url in cfg["urls"]:
        print(f"  -> {url}", flush=True)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2500)
        except Exception as e:  # noqa: BLE001
            print(f"     goto KO ({type(e).__name__}: {str(e)[:70]}) — on continue", flush=True)

    # Consentement (le cookie datadome n'est parfois posé qu'après).
    for sel in cfg["consent"]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=4000):
                btn.click(timeout=4000)
                print(f"  consentement accepté ({sel})", flush=True)
                break
        except Exception:  # noqa: BLE001
            continue

    t0 = time.time()
    last = None
    while time.time() - t0 < wait_s:
        val = _cookie(ctx, "datadome", site)
        if val and val != last:
            title = ""
            try:
                title = (page.title() or "")[:60]
            except Exception:  # noqa: BLE001
                pass
            blocked = "captcha" in page.url or "geo.captcha" in page.url
            print(f"  cookie datadome trouvé ({len(val)} car.) | url={page.url[:70]} | titre={title!r}", flush=True)
            if not blocked:
                page.close()
                return val
            print("  ⚠ page de challenge : résous-le dans la fenêtre Chrome...", flush=True)
            last = val
        try:
            page.wait_for_timeout(2000)
        except Exception:  # noqa: BLE001
            time.sleep(2)
    val = _cookie(ctx, "datadome", site)
    page.close()
    return val


def write_env(updates: dict[str, str]) -> None:
    lines: list[str] = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    for key, value in updates.items():
        pat = re.compile(rf"^{re.escape(key)}=")
        replaced = False
        for i, ln in enumerate(lines):
            if pat.match(ln):
                lines[i] = f"{key}={value}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}={value}")
    with open(ENV_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).strip() + "\n")
    print(f"\n{ENV_PATH} mis à jour : {', '.join(updates)}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", choices=sorted(SITES), action="append",
                    help="site(s) à traiter (défaut : tous)")
    ap.add_argument("--wait", type=int, default=150, help="attente max par site (s)")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()
    sites = args.site or sorted(SITES)

    from playwright.sync_api import sync_playwright

    updates: dict[str, str] = {}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=args.headless,
            channel="chrome",  # vrai Chrome : bien moins détecté que le Chromium PW
            locale="fr-FR",
            timezone_id="Europe/Paris",
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        ua = ctx.new_page().evaluate("navigator.userAgent")
        print(f"User-Agent navigateur : {ua}", flush=True)
        updates["SCRAPER_USER_AGENT"] = ua
        for site in sites:
            val = harvest(site, ctx, args.wait)
            if val:
                updates[SITES[site]["env"]] = val
                print(f"  ✅ {site} : cookie récupéré", flush=True)
            else:
                print(f"  ❌ {site} : aucun cookie datadome", flush=True)
        ctx.close()

    if len(updates) > 1:
        write_env(updates)
        return 0
    print("Aucun cookie récupéré.", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
