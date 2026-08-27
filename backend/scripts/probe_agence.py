#!/usr/bin/env python3
"""Teste si un site d'agence est exploitable, AVANT de l'ajouter à agences.yaml.

Répond à la seule question qui compte : « combien de biens utilisables ce site
donne-t-il, et par quelle voie ? » Un bien est utilisable s'il a un prix ET une commune
— sans commune il n'est pas géocodable, donc invisible pour les filtres de zone et le
scoring.

Les trois voies, dans l'ordre où l'ingestion les essaie :

    A. JSON-LD sur la page de liste   -> rien à coder, le cas idéal (rare)
    B. parser dédié au domaine        -> il faut l'écrire (agences_parsers.py)
    C. JSON-LD sur les fiches         -> rien à coder
    D. texte de la fiche (extracteur) -> rien à coder, le cas le plus fréquent

Usage :
    python scripts/probe_agence.py https://www.agence.fr/nos-biens
    python scripts/probe_agence.py --cap 5 URL1 URL2 ...
    python scripts/probe_agence.py --fichier candidats.txt
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from app.services.agences_ingest import _og, commune_depuis_titre  # noqa: E402
from app.services.extract import get_extractor  # noqa: E402
from app.services.geo_communes import main_commune_for_postcode  # noqa: E402
from app.services.agences_parsers import harvest_detail_links, parse_site  # noqa: E402
from app.sources.htmlutil import json_ld_items, realestate_fields  # noqa: E402

_UA = {"User-Agent": "Mozilla/5.0 (compatible; ImmobilierBot/0.1)"}


def _biens_json_ld(html: str) -> list[dict]:
    return [f for o in json_ld_items(html)
            if (f := realestate_fields(o)) and f.get("price") is not None]


def sonder(url: str, cap: int, delai: float) -> dict:
    """Sonde une page de liste. Renvoie un verdict par voie."""
    import time

    verdict = {"url": url, "voie": None, "biens": 0, "avec_commune": 0,
               "liens": 0, "note": "", "exemples": []}
    try:
        with httpx.Client(headers=_UA, timeout=25, follow_redirects=True) as c:
            page = c.get(url)
            page.raise_for_status()

            # Voie A — la liste porte déjà les biens.
            biens = _biens_json_ld(page.text)
            if biens:
                verdict.update(voie="A (JSON-LD sur la liste)", biens=len(biens),
                               avec_commune=sum(1 for b in biens if b.get("city")))
                verdict["exemples"] = [(b.get("price"), b.get("city")) for b in biens[:3]]
                return verdict

            # Voie B — un parser dédié existe déjà pour ce domaine.
            if parses := parse_site(url, page.text):
                verdict.update(voie="B (parser dédié)", biens=len(parses),
                               avec_commune=sum(1 for d in parses if d.get("commune")))
                verdict["exemples"] = [(d.get("prix"), d.get("commune")) for d in parses[:3]]
                return verdict

            # Voie C — suivre les fiches et lire LEUR JSON-LD.
            liens = harvest_detail_links(page.text, url, max_links=cap)
            verdict["liens"] = len(liens)
            if not liens:
                verdict["note"] = ("aucun lien de fiche : page rendue en JavaScript, "
                                   "ou URL de liste erronée")
                return verdict
            trouves = []
            for i, lien in enumerate(liens):
                if i:
                    time.sleep(delai)
                try:
                    fiche = c.get(lien)
                    fiche.raise_for_status()
                except Exception:  # noqa: BLE001
                    continue
                pris = False
                for b in _biens_json_ld(fiche.text):
                    commune = b.get("city") or commune_depuis_titre(
                        b.get("name") or b.get("description"))
                    trouves.append((b.get("price"), commune, "C"))
                    pris = True
                    break
                if pris:
                    continue
                # Voie D : la fiche n'a pas de JSON-LD immobilier -> lire son texte.
                titre = _og(fiche.text, "og:title") or lien
                for d in get_extractor().extract(titre, fiche.text, is_html=True):
                    if not d.get("prix"):
                        continue
                    commune = d.get("commune") or commune_depuis_titre(titre)
                    if not commune and d.get("code_postal"):
                        c_ = main_commune_for_postcode(d["code_postal"])
                        commune = c_.get("nom") if c_ else None
                    trouves.append((d["prix"], commune, "D"))
                    break
            voies = {v for _, _, v in trouves}
            libelle = {"C": "JSON-LD sur les fiches", "D": "texte des fiches"}
            verdict.update(
                voie=" + ".join(f"{v} ({libelle[v]})" for v in sorted(voies)) or None,
                biens=len(trouves),
                avec_commune=sum(1 for _, c_, _v in trouves if c_))
            verdict["exemples"] = [(p_, c_) for p_, c_, _v in trouves[:3]]
            if not trouves:
                verdict["note"] = (f"{len(liens)} liens suivis, aucun JSON-LD immobilier : "
                                   "site à parser à la main, ou liens de rubrique")
    except Exception as exc:  # noqa: BLE001
        verdict["note"] = f"{type(exc).__name__}: {str(exc)[:60]}"
    return verdict


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*", help="pages de liste d'annonces à sonder")
    ap.add_argument("--fichier", help="fichier texte, une URL par ligne")
    ap.add_argument("--cap", type=int, default=8, help="fiches suivies par site")
    ap.add_argument("--delai", type=float, default=0.7, help="secondes entre deux fiches")
    args = ap.parse_args()

    urls = list(args.urls)
    if args.fichier:
        with open(args.fichier, encoding="utf-8") as fh:
            urls += [l.strip() for l in fh if l.strip() and not l.startswith("#")]
    if not urls:
        ap.error("donner au moins une URL (ou --fichier)")

    exploitables = []
    for url in urls:
        v = sonder(url, args.cap, args.delai)
        etat = "OK  " if v["avec_commune"] else ("~   " if v["biens"] else "KO  ")
        print(f"{etat}{url}", flush=True)
        if v["voie"]:
            print(f"      voie {v['voie']} — {v['biens']} biens, "
                  f"{v['avec_commune']} avec commune", flush=True)
            for prix, commune in v["exemples"]:
                print(f"        {int(prix or 0):>9} €  {commune or '(commune inconnue)'}")
        if v["note"]:
            print(f"      {v['note']}", flush=True)
        if v["avec_commune"]:
            exploitables.append(url)

    print(f"\n{len(exploitables)}/{len(urls)} site(s) exploitables :")
    for u in exploitables:
        print(f"  {u}")
    print("\nÀ ajouter dans backend/agences.yaml (nom, set_id, sites).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
