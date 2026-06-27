#!/usr/bin/env python3
"""Récolte / rafraîchit un cookie Datadome via un navigateur persistant.

À lancer sur le poste de dev (IP résidentielle) : ouvre un Chromium **visible**, charge
le portail, te laisse résoudre l'éventuel captcha à la main, puis met le cookie en cache
(`<headless_state_dir>/datadome.json`). Les connecteurs API (Leboncoin...) le réutilisent
ensuite tant qu'il est frais (`DATADOME_MAX_AGE_MINUTES`).

Prérequis :
    pip install -r requirements-scrapers.txt
    playwright install chromium

Exemples
--------
    # Récolte le cookie Leboncoin (navigateur visible) :
    python scripts/refresh_datadome.py leboncoin

    # En précisant une URL et en forçant le mode visible/headless :
    python scripts/refresh_datadome.py seloger --url https://www.seloger.com/ --headed
"""

from __future__ import annotations

import argparse
import os
import sys

# `app` vit dans backend/app ; ce script dans backend/scripts. On ajoute backend au path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.config import get_settings  # noqa: E402
    from app.sources.headless import get_cached_datadome, harvest_datadome  # noqa: E402
except ModuleNotFoundError as exc:  # dépendances backend absentes -> mauvais interpréteur
    sys.exit(
        f"✗ Dépendance manquante ({exc.name}). Lance ce script avec le venv du backend, "
        "pas le Python système :\n"
        "    cd backend && source .venv/bin/activate && python scripts/refresh_datadome.py leboncoin\n"
        "  (ou ./.venv/bin/python scripts/refresh_datadome.py leboncoin)\n"
        "  Si le venv n'existe pas : python3.11 -m venv .venv && "
        ".venv/bin/pip install -r requirements.txt -r requirements-scrapers.txt && "
        ".venv/bin/playwright install chromium"
    )

# Portails connus : (domaine de cookie, URL de chargement par défaut).
TARGETS = {
    "leboncoin": ("leboncoin.fr", "https://www.leboncoin.fr/recherche?category=9"),
    "seloger": ("seloger.com", "https://www.seloger.com/"),
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Récolte un cookie Datadome via navigateur persistant.")
    ap.add_argument("target", choices=sorted(TARGETS), help="Portail cible.")
    ap.add_argument("--url", help="URL à charger (défaut : page de recherche du portail).")
    headed = ap.add_mutually_exclusive_group()
    headed.add_argument("--headed", dest="headed", action="store_true", help="Navigateur visible (défaut).")
    headed.add_argument("--headless", dest="headed", action="store_false", help="Sans fenêtre (déconseillé pour un 1er solve).")
    ap.set_defaults(headed=True)
    ap.add_argument("--timeout", type=int, default=None, help="Attente max du cookie (s).")
    args = ap.parse_args()

    domain, default_url = TARGETS[args.target]
    url = args.url or default_url
    settings = get_settings()

    print(f"→ Récolte du cookie Datadome pour {domain} ({'visible' if args.headed else 'headless'})…")
    print(f"  URL : {url}")
    if args.headed:
        print("  Résous le captcha si présent, puis laisse la fenêtre se fermer seule.")

    try:
        entry = harvest_datadome(
            url, domain, settings=settings, headed=args.headed, timeout_seconds=args.timeout
        )
    except RuntimeError as exc:  # Playwright absent
        print(f"✗ {exc}", file=sys.stderr)
        return 2

    if not entry:
        print("✗ Aucun cookie Datadome obtenu (captcha non résolu ou blocage).", file=sys.stderr)
        return 1

    cached = get_cached_datadome(domain, settings=settings)
    print(f"✓ Cookie récolté ({len(entry['cookie'])} car.) et mis en cache.")
    print(f"  Frais pendant {settings.datadome_max_age_minutes} min. Fichier : "
          f"{os.path.join(settings.headless_state_dir, 'datadome.json')}")
    if cached is None:
        print("  ⚠️  Cookie déjà périmé selon DATADOME_MAX_AGE_MINUTES — augmente la valeur.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
