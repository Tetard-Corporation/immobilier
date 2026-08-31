#!/usr/bin/env python3
"""Réchauffe le cache d'ensoleillement (backend/data/soleil_cache.json) via l'IGN.

Sans ce réchauffage, le critère `ensoleillement` sort en `pending` : l'export ne mesure
pas en direct (87 points d'altitude par bien, soit 4 requêtes groupées — trop cher pour un
catalogue entier), il lit le cache, exactement comme pour la distance à la mer.

Incrémental et résumable : les points déjà en cache sont sautés, le cache est écrit au fil
de l'eau.

Usage :
    python backend/scripts/warm_ensoleillement.py               # set « têtard »
    python backend/scripts/warm_ensoleillement.py --set 4 --limit 400
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

from app.services.export_static import _load_soleil_cache, _soleil  # noqa: E402


def _biens_du_set(set_id: int, par_zone: int = 0, score_min: float = 70.0) -> list[dict]:
    """Points à réchauffer, lus dans la BASE et non dans data.json.

    Après un export « pépites », data.json ne contient plus que le haut du panier : s'y
    fier ne réchaufferait rien pour les biens fraîchement collectés, qui n'existent qu'en
    base. On retombe sur data.json si la base est vide (conteneur réinitialisé).
    """
    try:
        from app.db import SessionLocal
        from app.models import Listing

        from app.models import FilterSet
        from app.services.entonnoir import candidats_par_zone

        db = SessionLocal()
        try:
            rows = [r for r in db.query(Listing).all()
                    if set_id in (r.set_ids or []) and r.latitude is not None
                    and r.longitude is not None]
            fs = db.get(FilterSet, set_id)
            zones = ((fs.criteria or {}).get("zones") or []) if fs else []
        finally:
            db.close()
        if rows:
            print(f"Source : base SQLite ({len(rows)} biens du set {set_id}).", flush=True)
            # 87 altitudes IGN par bien : mesurer tout le set prend des heures pour un
            # panier d'une quinzaine de pépites. `--par-zone` applique l'entonnoir au
            # réchauffage, exactement comme scripts/warm_tourisme.py.
            if par_zone and zones:
                rows = candidats_par_zone(rows, zones, par_zone=par_zone,
                                          score_min=score_min,
                                          log=lambda m: print(m, flush=True))
                print(f"  -> {len(rows)} candidats retenus.", flush=True)
            return [{"latitude": r.latitude, "longitude": r.longitude} for r in rows]
    except Exception as e:  # noqa: BLE001
        print(f"Base illisible ({type(e).__name__}) -> repli sur data.json.", flush=True)

    data = json.load(open(os.path.join(ROOT, "data", "data.json"), encoding="utf-8"))
    biens = [b for b in data["biens"]
             if str(set_id) in (b.get("scores_by_set") or {})
             and b.get("latitude") is not None and b.get("longitude") is not None]
    print(f"Source : data.json ({len(biens)} biens du set {set_id}).", flush=True)
    return biens


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", type=int, default=1, help="id du set à réchauffer")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--par-zone", type=int, default=0, dest="par_zone",
                    help="entonnoir : nb de biens mesurés par zone du set, les mieux "
                         "notés d'abord (0 = pas d'entonnoir)")
    ap.add_argument("--score-min", type=float, default=70.0, dest="score_min",
                    help="au-dessus de ce score d'investissement, mesuré quoi qu'il arrive")
    args = ap.parse_args()

    biens = _biens_du_set(args.set, args.par_zone, args.score_min)[: args.limit]
    cache = _load_soleil_cache()
    print(f"{len(biens)} biens du set {args.set} ; {len(cache)} points déjà en cache.", flush=True)

    done, echecs = 0, 0
    t0 = time.time()
    for i, b in enumerate(biens, 1):
        res = _soleil(b["latitude"], b["longitude"], cache, live=True)
        if res:
            done += 1
        else:
            echecs += 1
        if i % 10 == 0:
            print(f"  {i}/{len(biens)} ({done} mesurés, {time.time()-t0:.0f}s)", flush=True)

    heures = [v["soleil_hiver_h"] for v in cache.values() if isinstance(v.get("soleil_hiver_h"), (int, float))]
    if heures:
        heures.sort()
        med = heures[len(heures) // 2]
        sombres = sum(1 for h in heures if h < 1.5)
        print(f"Terminé : {len(cache)} points en cache ; médiane {med:.1f} h de soleil le "
              f"21 décembre, {sombres} point(s) sous 1h30 (ubac).", flush=True)
    # Un réchauffage à rendement nul ressemble trait pour trait à un réchauffage réussi si
    # on ne le dit pas : le critère resterait « pending » sans le moindre message.
    if echecs:
        print(f"⚠ {echecs} points non mesurés (IGN indisponible ou hors zone). Relancer : "
              f"les points déjà en cache ne sont pas redemandés.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
