#!/usr/bin/env python3
"""Réchauffe le cache d'accès depuis Paris (backend/data/trajet_cache.json).

Ce cache porte, pour chaque bien, la gare desservie depuis Paris qui minimise le
porte-à-porte, le temps de train MESURÉ par la SNCF sur cette liaison, le temps de
VOITURE mesuré par l'itinéraire IGN entre cette gare et le bien, et la gare la plus
proche (souvent une gare TER, qui n'est pas celle par laquelle on arrive de Paris).

Sans réchauffage, `near_gare` et `temps_acces` retombent sur une estimation à vol
d'oiseau — volontairement pessimiste, mais une estimation. C'est le même contrat que
l'ensoleillement et le tourisme : la mesure se paie ici, l'export relit le cache.

Contrairement à ces deux-là, la mesure est BON MARCHÉ : sept itinéraires IGN par bien,
~0,2 s chacun, sans clé. Tout le set y passe donc par défaut (~30 min pour 5 000 biens),
là où le réchauffage Overpass doit trier ses candidats.

Incrémental et résumable : les points déjà en cache sont sautés, le cache est écrit au
fil de l'eau. Deux biens du même hameau partagent une entrée (clé à 3 décimales, ~100 m).

Usage :
    python backend/scripts/warm_trajet.py                 # tout le set têtard
    python backend/scripts/warm_trajet.py --set 4         # le set breton
    python backend/scripts/warm_trajet.py --par-zone 40 --score-min 65   # entonnoir
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


def _candidats_catalogue(chemin: str) -> list[tuple[float, float]]:
    """Les points à mesurer, lus dans la SAUVEGARDE TEXTE du catalogue.

    Existe pour pouvoir réchauffer pendant qu'une collecte tourne : la base SQLite est
    alors en écriture, et une grosse lecture concurrente risque de la verrouiller — on ne
    fait pas courir ce risque à une collecte de plusieurs heures pour une mesure qui se
    relance en une commande. Le cache étant indexé par coordonnées arrondies, réchauffer
    depuis le catalogue sert tous les biens du même hameau, publiés ou non.
    """
    points = []
    with open(chemin, encoding="utf-8") as fh:
        for ligne in fh:
            try:
                b = json.loads(ligne)
            except json.JSONDecodeError:
                continue
            if b.get("latitude") is not None and b.get("longitude") is not None:
                points.append((b["latitude"], b["longitude"]))
    print(f"{len(points)} biens géolocalisés dans {os.path.basename(chemin)}.", flush=True)
    return points


def _candidats(set_id: int, par_zone: int | None, score_min: float) -> list[tuple[float, float]]:
    """Les points à mesurer, lus dans la BASE (data.json n'a que les biens publiés)."""
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
                and _dans_la_zone(r, crit.get("zone"))]
    finally:
        db.close()
    print(f"{len(rows)} biens du set {set_id} en base, {len(zones)} zones déclarées.", flush=True)
    if par_zone is None or not zones:
        return [(r.latitude, r.longitude) for r in rows]
    retenus = candidats_par_zone(rows, zones, par_zone=par_zone, score_min=score_min,
                                 log=lambda m: print(m, flush=True))
    return [(r.latitude, r.longitude) for r in retenus]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", type=int, default=1, help="id du set à réchauffer")
    ap.add_argument("--par-zone", type=int, default=None, dest="par_zone",
                    help="entonnoir : nb de biens par zone (défaut : aucun, tout le set)")
    ap.add_argument("--score-min", type=float, default=65.0, dest="score_min")
    ap.add_argument("--catalogue", nargs="?", const=os.path.join(ROOT, "backend", "data", "catalogue.jsonl"),
                    default=None, help="lire les points dans le dump du catalogue plutôt "
                                       "que dans la base (à faire pendant une collecte)")
    ap.add_argument("--limit", type=int, default=100000)
    args = ap.parse_args()

    n_gares = trajet.gares_chargees()
    if not n_gares:
        print("ABANDON : référentiel des gares absent. "
              "Lancer d'abord `python scripts/build_gares_dataset.py`.", flush=True)
        return 1
    hubs = len([g for g in trajet._gares() if g["paris_min"]])
    print(f"Référentiel : {n_gares} gares voyageurs, dont {hubs} avec une durée "
          f"Paris mesurée.", flush=True)

    points = (_candidats_catalogue(args.catalogue) if args.catalogue
              else _candidats(args.set, args.par_zone, args.score_min))[: args.limit]
    cache = trajet.charger_cache()
    a_faire = {}
    for lat, lon in points:
        k = trajet.cle(lat, lon)
        if k not in cache:
            a_faire[k] = (lat, lon)
    print(f"\n{len(points)} points candidats · {len(cache)} déjà en cache · "
          f"{len(a_faire)} à mesurer (~{len(a_faire) * 1.4 / WORKERS / 60:.0f} min).", flush=True)
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
            if i % 50 == 0 or i == len(futs):
                tmp = f"{trajet.CACHE_PATH}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(cache, fh, ensure_ascii=False)
                os.replace(tmp, trajet.CACHE_PATH)
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
