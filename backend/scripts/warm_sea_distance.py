#!/usr/bin/env python3
"""Réchauffe le cache de distance à la mer (backend/data/sea_cache.json) pour les biens
côtiers, via l'IGN (Overpass étant injoignable depuis le conteneur cloud).

Incrémental et résumable : les points déjà en cache sont sautés, le cache est écrit au
fil de l'eau. Une fois réchauffé, l'export lit le cache (rapide) et le critère
`distance_mer` est actif.

Usage :
    python backend/scripts/warm_sea_distance.py                 # set « Littoral breton »
    python backend/scripts/warm_sea_distance.py --set 4 --limit 400
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services.export_static import _load_sea_cache, _sea_distance  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", type=int, default=4, help="id du set à réchauffer")
    ap.add_argument("--limit", type=int, default=1000)
    args = ap.parse_args()

    data = json.load(open(os.path.join(ROOT, "data", "data.json"), encoding="utf-8"))
    biens = [b for b in data["biens"]
             if str(args.set) in (b.get("scores_by_set") or {})
             and b.get("latitude") is not None and b.get("longitude") is not None]
    biens = biens[: args.limit]
    cache = _load_sea_cache()
    print(f"{len(biens)} biens du set {args.set} ; {len(cache)} points déjà en cache.", flush=True)

    done = 0
    t0 = time.time()
    for i, b in enumerate(biens, 1):
        res = _sea_distance(b["latitude"], b["longitude"], cache, live=True)
        if res:
            done += 1
        if i % 20 == 0:
            print(f"  {i}/{len(biens)} ({done} calculés, {time.time()-t0:.0f}s)", flush=True)
    # distribution rapide
    near = sum(1 for v in cache.values() if isinstance(v.get("dist_mer_m"), (int, float)) and v["dist_mer_m"] <= 1000)
    print(f"Terminé : {len(cache)} points en cache ; {near} à ≤1 km de la mer.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
