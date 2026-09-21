#!/usr/bin/env python3
"""Réchauffe le cache « montagne alentour » (backend/data/montagne_cache.json).

Mesure, autour de chaque point, le plus haut sommet à vingt kilomètres et le dénivelé
qui le sépare du bien — ce que le critère `relief_mountain` note désormais, à la place de
la seule altitude du bien (voir `app/services/montagne.py`).

41 altitudes IGN par point, soit 2 requêtes groupées. Le cache est indexé au kilomètre
(2 décimales) et non à la centaine de mètres : le sommet des environs ne change pas d'un
hameau à l'autre, alors que le nombre d'appels, lui, serait multiplié par cinq.

Sans réchauffage, le critère retombe sur l'altitude du bien — l'ancien calcul, celui qui
donnait 1,00 à un plateau nu à 900 m et 0,38 à un village alpin de fond de vallée.

Usage :
    python backend/scripts/warm_montagne.py --catalogue
    python backend/scripts/warm_montagne.py --set 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services.export_static import _MONTAGNE_CACHE, _load_montagne_cache, _query_montagne  # noqa: E402

WORKERS = int(os.environ.get("WARM_MONTAGNE_WORKERS", "3"))


def _cle(lat: float, lon: float) -> str:
    return f"{round(lat, 2)},{round(lon, 2)}"


def _points(catalogue: str | None, set_id: int) -> list[tuple[float, float]]:
    if catalogue:
        pts = []
        with open(catalogue, encoding="utf-8") as fh:
            for ligne in fh:
                try:
                    b = json.loads(ligne)
                except json.JSONDecodeError:
                    continue
                if b.get("latitude") is not None and b.get("longitude") is not None:
                    pts.append((b["latitude"], b["longitude"]))
        print(f"{len(pts)} biens géolocalisés dans {os.path.basename(catalogue)}.", flush=True)
        return pts
    from app.db import SessionLocal
    from app.models import Listing

    db = SessionLocal()
    try:
        rows = [r for r in db.query(Listing).all()
                if r.latitude is not None and r.longitude is not None
                and (not r.set_ids or set_id in (r.set_ids or []))]
    finally:
        db.close()
    print(f"{len(rows)} biens du set {set_id} en base.", flush=True)
    return [(r.latitude, r.longitude) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", type=int, default=1)
    ap.add_argument("--catalogue", nargs="?",
                    const=os.path.join(ROOT, "backend", "data", "catalogue.jsonl"), default=None,
                    help="lire les points dans le dump du catalogue plutôt que dans la base")
    ap.add_argument("--limit", type=int, default=100000)
    args = ap.parse_args()

    points = _points(args.catalogue, args.set)[: args.limit]
    cache = _load_montagne_cache()
    a_faire = {}
    for lat, lon in points:
        k = _cle(lat, lon)
        if k not in cache:
            a_faire[k] = (lat, lon)
    print(f"\n{len(points)} points · {len(cache)} déjà en cache · {len(a_faire)} à mesurer.",
          flush=True)
    if not a_faire:
        return 0

    faits, echecs, t0 = 0, 0, time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_query_montagne, lat, lon): k for k, (lat, lon) in a_faire.items()}
        for i, f in enumerate(as_completed(futs), 1):
            k = futs[f]
            try:
                res = f.result()
            except Exception:
                res = None
            if res is None:
                echecs += 1
            else:
                cache[k] = res
                faits += 1
            if i % 40 == 0 or i == len(futs):
                tmp = f"{_MONTAGNE_CACHE}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(cache, fh)
                os.replace(tmp, _MONTAGNE_CACHE)
                reste = (len(futs) - i) * (time.time() - t0) / max(i, 1)
                print(f"  {i}/{len(futs)} · {faits} mesurés · {echecs} échecs · "
                      f"reste ~{reste / 60:.0f} min", flush=True)

    print(f"\nCache : {len(cache)} points. {faits} mesurés, {echecs} abandonnés "
          f"en {(time.time() - t0) / 60:.0f} min.", flush=True)
    if echecs:
        print(f"⚠ {echecs} points abandonnés — relancer (le script est incrémental).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
