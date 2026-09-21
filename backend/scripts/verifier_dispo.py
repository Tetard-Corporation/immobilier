#!/usr/bin/env python3
"""Retire les annonces qui ne sont plus en ligne.

« Plus dispo », « Annonce plus disponible, dommage » : quatre commentaires sur
quarante et un, soit le deuxième reproche du groupe après l'accès. Une annonce morte
coûte plus qu'un mauvais bien — on l'ouvre, on la regarde, on vote, et il n'y avait rien
à voir.

Ce que le script considère comme une preuve de disparition, et rien d'autre :

- **404 / 410** — le portail dit lui-même que la page n'existe plus ;
- une **redirection vers une page de liste ou de recherche** — le portail a remplacé la
  fiche par son moteur, ce qu'il fait quand l'annonce est retirée ;
- une page qui **porte la mention** « annonce expirée », « n'est plus disponible », etc.

Tout le reste est traité comme INCONNU, et un bien inconnu est gardé :

- **403 et captcha** (leboncoin, seloger derrière Datadome) : sans cookie valide, toutes
  leurs annonces répondraient pareil, et on supprimerait le catalogue entier de deux
  sources sur un défaut d'authentification ;
- **erreur réseau, 5xx, délai dépassé** : la panne est de notre côté ou du leur.

Un faux positif supprime un bien que personne ne reverra ; un faux négatif laisse une
annonce morte de plus dans une liste qui en contient déjà. Le premier coûte plus cher.

Usage :
    python backend/scripts/verifier_dispo.py --dry-run          # biens publiés
    python backend/scripts/verifier_dispo.py --tout --workers 6
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.db import SessionLocal  # noqa: E402
from app.models import Listing, SavedListing  # noqa: E402

# Sources sans anti-bot : on peut lire leurs pages, donc on peut conclure. Leboncoin et
# SeLoger sont exclus par construction — derrière Datadome, leur réponse ne dit rien de
# l'annonce, seulement de notre cookie.
SOURCES_LISIBLES = {"bienici", "paruvendu", "notaires", "agences"}

_MORTE = re.compile(
    r"(annonce (?:est )?(?:expirée|expiree|archivée|archivee|supprimée|supprimee|"
    r"n['’]est plus (?:disponible|en ligne)|introuvable)"
    r"|ce bien (?:a été vendu|n['’]est plus disponible)"
    r"|cette annonce n['’]est plus"
    r"|le bien que vous recherchez"
    r"|annonce non disponible"
    r"|page introuvable)", re.I)
# Une fiche remplacée par la page de recherche du portail : l'URL finale ne porte plus
# d'identifiant d'annonce.
_LISTE = re.compile(r"/(recherche|annonces|resultat|search|acheter)/?($|\?)", re.I)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def verdict(client: httpx.Client, url: str) -> tuple[str, str]:
    """('morte'|'vivante'|'inconnu', raison)."""
    try:
        r = client.get(url, follow_redirects=True, timeout=20,
                       headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"})
    except Exception as e:
        return "inconnu", f"réseau ({type(e).__name__})"
    if r.status_code in (404, 410):
        return "morte", f"HTTP {r.status_code}"
    if r.status_code in (401, 403, 429) or "captcha-delivery" in str(r.url):
        return "inconnu", f"bloqué (HTTP {r.status_code})"
    if r.status_code >= 500:
        return "inconnu", f"HTTP {r.status_code}"
    if str(r.url) != url and _LISTE.search(str(r.url)):
        return "morte", "redirigée vers la page de recherche"
    extrait = r.text[:400000]
    trouve = _MORTE.search(extrait)
    if trouve:
        return "morte", f"« {trouve.group(0)[:60]} »"
    return "vivante", f"HTTP {r.status_code}"


def _biens(db, tout: bool) -> list[Listing]:
    rows = [r for r in db.query(Listing).all()
            if r.url and r.source in SOURCES_LISIBLES]
    if tout:
        return rows
    publies = set()
    chemin = os.path.join(ROOT, "data", "data.json")
    if os.path.exists(chemin):
        d = json.load(open(chemin, encoding="utf-8"))
        publies = {b["external_id"] for b in d["biens"]}
    return [r for r in rows if r.external_id in publies]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", dest="dry")
    ap.add_argument("--tout", action="store_true", help="tout le catalogue, pas seulement les publiés")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=100000)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = _biens(db, args.tout)[: args.limit]
        favoris = {sv.listing_id for sv in db.query(SavedListing).all()}
        print(f"{len(rows)} annonces à vérifier "
              f"({', '.join(sorted(SOURCES_LISIBLES))} ; leboncoin et seloger exclus, "
              f"leur réponse ne dit rien de l'annonce).", flush=True)
        if not rows:
            return 0

        mortes, vivantes, inconnues, t0 = [], 0, 0, time.time()
        with httpx.Client(http2=False) as client:
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs = {ex.submit(verdict, client, r.url): r for r in rows}
                for i, f in enumerate(as_completed(futs), 1):
                    r = futs[f]
                    etat, raison = f.result()
                    if etat == "morte":
                        mortes.append((r, raison))
                    elif etat == "vivante":
                        vivantes += 1
                    else:
                        inconnues += 1
                    if i % 50 == 0 or i == len(futs):
                        reste = (len(futs) - i) * (time.time() - t0) / max(i, 1)
                        print(f"  {i}/{len(futs)} · {len(mortes)} mortes · {vivantes} vivantes · "
                              f"{inconnues} indécidables · reste ~{reste / 60:.0f} min", flush=True)

        gardes = [(r, why) for r, why in mortes if r.id in favoris]
        a_jeter = [r for r, _ in mortes if r.id not in favoris]
        print(f"\n{len(mortes)} annonces mortes · {vivantes} en ligne · {inconnues} indécidables"
              f"{f' · {len(gardes)} mortes mais en favori, gardées' if gardes else ''}")
        for r, why in mortes[:10]:
            print(f"    {r.commune} — {r.source} — {why}")
        if args.dry:
            print("\n(--dry-run : rien n'a été supprimé)")
            return 0
        for i in range(0, len(a_jeter), 500):
            lot = [r.id for r in a_jeter[i:i + 500]]
            db.query(Listing).filter(Listing.id.in_(lot)).delete(synchronize_session=False)
        db.commit()
        print(f"  supprimées. {db.query(Listing).count()} biens restants.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
