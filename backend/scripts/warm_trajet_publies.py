#!/usr/bin/env python3
"""Réchauffe l'accès des seuls biens PUBLIÉS, lus dans `data/data.json`.

Le réchauffage du catalogue entier (`warm_trajet.py --catalogue`) parcourt 5 400 points
dans un ordre quelconque : une partie des biens publiés peut rester en estimation pendant
qu'il mesure des biens que le site ne montre pas. Or c'est sur la page que l'écart se
voit — un bien estimé porte un « ≈ » et, l'estimation étant volontairement pessimiste, il
est classé plus bas qu'un voisin mesuré sans rien avoir de moins.

À lancer juste avant l'export final, quand le panier publié est connu.
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

from app.services import trajet  # noqa: E402

WORKERS = int(os.environ.get("WARM_TRAJET_WORKERS", "4"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data", "data.json"))
    args = ap.parse_args()

    d = json.load(open(args.data, encoding="utf-8"))
    cache = trajet.charger_cache()
    a_faire = {}
    for b in d["biens"]:
        if b.get("latitude") is None or b.get("longitude") is None:
            continue
        k = trajet.cle(b["latitude"], b["longitude"])
        if k not in cache:
            a_faire[k] = (b["latitude"], b["longitude"])
    print(f"{len(d['biens'])} biens publiés · {len(cache)} points en cache · "
          f"{len(a_faire)} à mesurer (~{len(a_faire) * 1.8 / WORKERS / 60:.0f} min).", flush=True)
    if not a_faire:
        return 0

    faits, echecs, t0 = 0, 0, time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(trajet.mesurer, lat, lon): k for k, (lat, lon) in a_faire.items()}
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
            if i % 25 == 0 or i == len(futs):
                tmp = f"{trajet.CACHE_PATH}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(cache, fh, ensure_ascii=False)
                os.replace(tmp, trajet.CACHE_PATH)
                print(f"  {i}/{len(futs)} · {faits} mesurés · {echecs} échecs", flush=True)

    print(f"\nCache : {len(cache)} points. {faits} mesurés, {echecs} abandonnés "
          f"en {(time.time() - t0) / 60:.0f} min.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
