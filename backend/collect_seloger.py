"""Collecte SeLoger (one-shot) : SERP multi-communes -> géocodage -> enrichissement
parallèle -> scoring par set -> upsert -> export data.json + photos.

À exécuter EN LOCAL avec un cookie Datadome frais (cf. docs/OPERATIONS.md §2 et
scripts/datadome_cookies.py) : sans lui la source se déclare indisponible.

SeLoger n'indexe qu'à la commune et n'accepte que ses identifiants internes
(`AD08FR<n>`), pas les codes postaux. On aligne donc les zones sur celles de
`collect_leboncoin.py` en résolvant, pour chaque code postal, la commune la plus
peuplée -> son `placeId` (mis en cache dans data/seloger_places.json).

Usage :
    python collect_seloger.py --zone ploemeur      # une zone
    python collect_seloger.py                      # toutes les zones
    python collect_seloger.py --dry-run            # collecte seule, rien écrit
    python collect_seloger.py --no-export          # upsert sans ré-export data.json
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.db import SessionLocal, init_db
from app.enrichment import enrich_listing
from app.models import Listing
from app.schemas import SearchCriteria
from app.seed import seed_from_data_json, seed_if_empty
from app.services.geo_communes import main_commune_for_postcode
from app.services.search import upsert_listing
from app.sources.scraper import ScraperBlocked
from app.sources.seloger import SeLogerSource
from collect_leboncoin import PLOEMEUR_ZIPS, TETARD_ZIPS

# Mêmes zones que la collecte Leboncoin, pour que les sets restent comparables.
ZONES = {
    "tetard": {"zips": TETARD_ZIPS, "set_ids": [1, 2], "types": ["maison"],
               "prix_max": 300000, "target": 300, "pages": 8},
    # Terrains ≤400k ET maisons ≤400k AVEC terrain (longères/pépites à rénover).
    "ploemeur": {"zips": PLOEMEUR_ZIPS, "set_ids": [4], "types": ["terrain", "maison"],
                 "prix_max": 400000, "target": 110, "pages": 4, "min_terrain_maison": 300},
}

# SeLoger fusionne les résultats de plusieurs communes en une requête, ce qui économise
# beaucoup d'appels — mais une URL portant 65 identifiants est fragile. D'où des lots.
_PLACES_PER_QUERY = 15


def resolve_places(src: SeLogerSource, zips: list[str]) -> tuple[list[str], dict]:
    """Résout les codes postaux d'une zone en placeIds SeLoger (cache disque).

    Renvoie (placeIds, {commune: {lat, lon, code}}) : les cartes de la SERP ne portant
    pas de coordonnées, on réutilise les centroïdes de commune pour géolocaliser."""
    place_ids: list[str] = []
    centres: dict[str, dict] = {}
    for cp in zips:
        commune = main_commune_for_postcode(cp)
        if not commune or not commune.get("nom"):
            print(f"  {cp} : commune introuvable", flush=True)
            continue
        centres[commune["nom"]] = {"lat": commune["lat"], "lon": commune["lon"],
                                  "code": commune["code"]}
        pid = src.place_id(commune["nom"], cp[:2])
        if pid:
            place_ids.append(pid)
        else:
            print(f"  {cp} {commune['nom']} : pas de placeId SeLoger", flush=True)
    return place_ids, centres


def collect_zone(src: SeLogerSource, name: str, zone: dict) -> list:
    """Toutes les pages d'une zone, dédupliquées et filtrées (prix, type, terrain)."""
    print(f"\n[{name}] résolution des {len(zone['zips'])} codes postaux...", flush=True)
    place_ids, centres = resolve_places(src, zone["zips"])
    print(f"[{name}] {len(place_ids)} placeIds résolus", flush=True)
    if not place_ids:
        return []

    crit = SearchCriteria(property_types=zone["types"], prix_max=zone["prix_max"])
    seen: dict[str, object] = {}
    chunks = [place_ids[i:i + _PLACES_PER_QUERY]
              for i in range(0, len(place_ids), _PLACES_PER_QUERY)]
    for ci, chunk in enumerate(chunks, 1):
        for page in range(1, zone.get("pages", 3) + 1):
            try:
                items = src.search_place(crit, chunk, page=page)
            except ScraperBlocked as e:
                print(f"[{name}] BLOQUÉ (lot {ci}, page {page}) : {str(e)[:70]}", flush=True)
                print("       cookie à régénérer : python scripts/datadome_cookies.py", flush=True)
                return list(seen.values())
            except Exception as e:  # noqa: BLE001
                print(f"[{name}] lot {ci} page {page} KO : {type(e).__name__}: {str(e)[:60]}", flush=True)
                break
            if not items:
                break
            new = 0
            for it in items:
                if not it.external_id or it.external_id in seen:
                    continue
                if it.prix is None or it.prix > zone["prix_max"]:
                    continue
                min_terr = zone.get("min_terrain_maison")
                if min_terr and it.type_bien == "maison" and (it.surface_terrain or 0) < min_terr:
                    continue
                centre = centres.get(it.commune or "")
                if centre:
                    it.latitude, it.longitude = centre["lat"], centre["lon"]
                    it.code_commune = it.code_commune or centre["code"]
                seen[it.external_id] = it
                new += 1
            print(f"[{name}] lot {ci}/{len(chunks)} p{page} : {len(items)} cartes, "
                  f"{new} retenues (cumul {len(seen)})", flush=True)
    return list(seen.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone", choices=sorted(ZONES), action="append",
                    help="zone(s) a collecter (defaut : toutes)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true", help="collecte seule, rien ecrit")
    ap.add_argument("--no-export", action="store_true", help="upsert sans re-export data.json")
    ap.add_argument("--reseed", action="store_true",
                    help="RECONSTRUIT la base depuis data.json (DESTRUCTIF : efface les "
                         "biens non encore exportes, y compris ceux d'une autre session)")
    args = ap.parse_args()

    src = SeLogerSource()
    if not src.available:
        print("SeLoger indisponible : ni PROXY_URL ni SELOGER_DATADOME.", flush=True)
        print("  -> python scripts/datadome_cookies.py --site seloger", flush=True)
        return 1

    zones = {k: ZONES[k] for k in (args.zone or sorted(ZONES))}

    if args.dry_run:
        for name, z in zones.items():
            items = collect_zone(src, name, z)
            print(f"\n[{name}] {len(items)} biens collectes (dry-run)")
            for it in items[:12]:
                print(f"   {str(it.type_bien):9s} {str(it.prix):>9s} EUR  bati={str(it.surface_bati):>7s} "
                      f"terrain={str(it.surface_terrain):>8s}  {str(it.commune):18s} {it.url}")
        return 0

    init_db()
    # La base SQLite est un store de travail PARTAGE : une autre session (ou une autre
    # collecte) peut y avoir des biens pas encore exportes vers data.json. Un
    # seed_from_data_json() inconditionnel les EFFACE — vécu, 200 biens perdus. On ne
    # seede donc que si la base est vide, et le mode destructif est explicite.
    if args.reseed:
        print("Reconstruction de la base depuis data.json (--reseed)...", flush=True)
        print(f"  -> {seed_from_data_json()}", flush=True)
    else:
        recap = seed_if_empty()
        print(f"Base {'seedee depuis data.json : ' + str(recap) if recap else 'deja peuplee : seed saute'}",
              flush=True)
    db = SessionLocal()
    existing = {e for (e,) in db.query(Listing.external_id).filter(Listing.source == "seloger").all()}

    collected: dict[str, tuple] = {}
    for name, z in zones.items():
        kept = 0
        for it in collect_zone(src, name, z):
            if it.external_id in existing or it.external_id in collected:
                continue
            collected[it.external_id] = (it, z["set_ids"])
            kept += 1
            if kept >= z["target"]:
                break
        print(f"[{name}] {kept} biens neufs retenus", flush=True)

    todo = list(collected.values())
    print(f"\nEnrichissement de {len(todo)} biens ({args.workers} workers)...", flush=True)

    def work(pair):
        item, set_ids = pair
        return enrich_listing(item), set_ids

    t0 = time.time()
    enriched = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, p): p for p in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                enriched.append(f.result())
            except Exception as e:  # noqa: BLE001
                print(f"  enrich KO: {type(e).__name__}: {str(e)[:60]}", flush=True)
            if i % 10 == 0:
                print(f"  {i}/{len(todo)} ({time.time()-t0:.0f}s)", flush=True)
    print(f"  enrichis: {len(enriched)} en {time.time()-t0:.0f}s", flush=True)

    for item, set_ids in enriched:
        row = upsert_listing(db, item)
        row.set_ids = set_ids
    db.commit()
    n = db.query(Listing).filter(Listing.source == "seloger").count()
    print(f"\nEn base : {n} biens seloger.", flush=True)

    if not args.no_export:
        from app.services.export_static import export_to_dir
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        print(f"\nExport vers {os.path.abspath(data_dir)} (photos incluses)...", flush=True)
        t1 = time.time()
        stats = export_to_dir(db, data_dir, download_photos=True)
        print(f"  export OK en {time.time()-t1:.0f}s : {stats}", flush=True)
    db.close()
    print("TERMINE.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
