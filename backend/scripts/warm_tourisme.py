#!/usr/bin/env python3
"""Réchauffe le cache d'attractivité locative (backend/data/tourisme_cache.json).

Sans ce réchauffage, le critère `attractivite_airbnb` sort en `pending` : l'export ne
mesure pas en direct (une requête Overpass de ~5 s par point, sur cinq rayons), il lit le
cache — exactement comme la distance à la mer et l'ensoleillement. Et un critère `pending`
est *exclu* du score au lieu de le baisser : un bien non mesuré n'est pas pénalisé, il est
jugé sur les autres critères, ce qui le fait monter. Rien ne le signale.

Incrémental et résumable : les points déjà en cache sont sautés, le cache est écrit au fil
de l'eau.

**Réchauffer les candidats, pas le catalogue.** 5 300 biens du set × 5 s = 7 heures, pour
un panier d'une quinzaine de pépites plus un témoin par zone. `--par-zone` applique
l'entonnoir au réchauffage : on ne mesure que le haut du classement de chaque zone, plus
tout ce qui dépasse un score plancher. C'est le même raisonnement que
`services/entonnoir.py` — filtrer avant de payer la mesure chère.

Usage :
    python backend/scripts/warm_tourisme.py                    # candidats du set têtard
    python backend/scripts/warm_tourisme.py --par-zone 25 --score-min 70
    python backend/scripts/warm_tourisme.py --tout --limit 2000  # sans entonnoir
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

from app.services.export_static import (  # noqa: E402
    _load_tourisme_cache, _query_tourisme, _TOURISME_CACHE, verifier_overpass,
)

# Overpass n'accorde que ~2 slots d'exécution par IP. Au-delà il répond 406/429 et,
# l'exception étant avalée, les résultats sont perdus EN SILENCE (cf. warm.py).
WORKERS = int(os.environ.get("WARM_WORKERS", "2"))
TRIES = int(os.environ.get("WARM_TRIES", "3"))


def _cle(lat: float, lon: float) -> str:
    return f"{round(lat, 4)},{round(lon, 4)}"


def _candidats(set_id: int, par_zone: int, score_min: float, tout: bool) -> list[tuple[float, float]]:
    """Les points à mesurer, lus dans la BASE (data.json n'a plus que les pépites)."""
    from app.db import SessionLocal
    from app.models import FilterSet, Listing
    from app.services.entonnoir import candidats_par_zone
    from app.services.export_static import _dans_la_zone

    db = SessionLocal()
    try:
        fs = db.get(FilterSet, set_id)
        crit = (fs.criteria or {}) if fs else {}
        zones = crit.get("zones") or []
        rows = [r for r in db.query(Listing).all()
                if (not r.set_ids or set_id in (r.set_ids or []))
                and r.latitude is not None and r.longitude is not None
                # Le set déclare aussi un PÉRIMÈTRE (ici : à l'est de l'axe
                # Lyon-Valence). Un bien hors périmètre n'est jamais noté pour ce set :
                # le mesurer, c'est payer cinq secondes pour un score qui n'existera pas.
                and _dans_la_zone(r, crit.get("zone"))]
    finally:
        db.close()
    print(f"{len(rows)} biens du set {set_id} en base, {len(zones)} zones déclarées.", flush=True)

    if tout or not zones:
        return [(r.latitude, r.longitude) for r in rows]
    retenus = candidats_par_zone(rows, zones, par_zone=par_zone, score_min=score_min,
                                log=lambda m: print(m, flush=True))
    return [(r.latitude, r.longitude) for r in retenus]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", type=int, default=1, help="id du set à réchauffer")
    ap.add_argument("--par-zone", type=int, default=25, dest="par_zone",
                    help="nb de biens mesurés par zone, les mieux notés d'abord")
    ap.add_argument("--score-min", type=float, default=70.0, dest="score_min",
                    help="au-dessus de ce score d'investissement, mesuré quoi qu'il arrive")
    ap.add_argument("--tout", action="store_true", help="pas d'entonnoir : tout le set")
    ap.add_argument("--limit", type=int, default=100000)
    args = ap.parse_args()

    ok, message = verifier_overpass()
    print(f"Instance Overpass — {message}", flush=True)
    if not ok:
        print("ABANDON : une instance qui ne couvre pas la France remplirait le cache de "
              "zéros indiscernables de vrais zéros (cf. docs/OPERATIONS.md §4).", flush=True)
        return 1

    points = _candidats(args.set, args.par_zone, args.score_min, args.tout)[: args.limit]
    cache = _load_tourisme_cache()
    a_faire = {}
    for lat, lon in points:
        k = _cle(lat, lon)
        if k not in cache:
            a_faire[k] = (lat, lon)
    print(f"\n{len(points)} points candidats · {len(cache)} déjà en cache · "
          f"{len(a_faire)} à mesurer (~{len(a_faire) * 5 / WORKERS / 60:.0f} min).", flush=True)
    if not a_faire:
        return 0

    def _mesurer(item):
        k, (lat, lon) = item
        for essai in range(TRIES):
            res = _query_tourisme(lat, lon)
            if res is not None:
                return k, res
            time.sleep(5 * (essai + 1))
        return k, None

    faits, echecs, t0 = 0, 0, time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_mesurer, it) for it in a_faire.items()]
        for i, f in enumerate(as_completed(futs), 1):
            k, res = f.result()
            if res is None:
                echecs += 1
            else:
                cache[k] = res
                faits += 1
            if i % 25 == 0 or i == len(futs):
                tmp = f"{_TOURISME_CACHE}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(cache, fh)
                os.replace(tmp, _TOURISME_CACHE)
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
