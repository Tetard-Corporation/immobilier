"""Collecte Leboncoin (one-shot) : search multi-CP -> enrichissement parallèle ->
scoring par set -> upsert -> export data.json + photos.

Usage :
    python collect_leboncoin.py --test          # petit lot Pauline (validation pipeline)
    python collect_leboncoin.py                 # collecte complète
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.db import SessionLocal, init_db
from app.enrichment import enrich_listing
from app.models import Listing
from app.seed import seed_from_data_json
from app.services.enrich import annotate
from app.services.export_static import _detect_viager
from app.services.search import upsert_listing
from app.sources.leboncoin import LeboncoinSource, _APP_TO_RET

# --- Zones -> (zips, set_ids, types, prix_max) --------------------------------
PAULINE_ZIPS = ["29600", "29250", "29252", "29660", "29630", "29241", "29670", "29610"]
TETARD_ZIPS = ["26110", "01200", "07270", "26150", "73340", "73630", "73100", "07200",
    "26300", "73410", "07140", "07110", "26230", "26320", "26340", "26750", "43430",
    "43520", "73000", "73240", "73460", "07410", "07600", "26130", "26170", "73220",
    "73250", "73390", "07120", "07240", "07260", "07380", "07320", "07460", "01630",
    "07400", "26140", "26200", "26390", "26400", "26460", "26500", "26510", "26740",
    "42560", "42750", "43190", "43200", "48800", "73130", "73420", "73520", "73800",
    "07190", "07170", "07800", "07150", "07210", "07230", "01260", "07430", "07530",
    "07440", "01550", "07360", "07160", "07330"]

# Zone « Ploemeur » (set 4) : Ploemeur + littoral morbihannais / finistérien sud.
# Codes postaux déduits des biens bienici déjà collectés sur ce set, complétés par
# les communes côtières mitoyennes (Étel, Moëlan-sur-Mer, Riec-sur-Bélon).
PLOEMEUR_ZIPS = ["56270", "56100", "56600", "56260", "56530", "56850", "56620", "56700",
    "56520", "56590", "56680", "56670", "56570", "56290", "56320", "56410", "56440",
    "29360", "29300", "29350", "29340"]

ZONES = {
    "pauline": {"zips": PAULINE_ZIPS, "set_ids": [3], "types": ["maison", "appartement"],
                "prix_max": 170000, "target": 60},
    "tetard": {"zips": TETARD_ZIPS, "set_ids": [1, 2], "types": ["maison"],
               "prix_max": 300000, "target": 300},
    # Terrains ≤400k ET maisons ≤400k AVEC terrain (longères/pépites à rénover) :
    # une maison sans terrain n'a pas d'intérêt pour ce set, d'où min_terrain_maison.
    "ploemeur": {"zips": PLOEMEUR_ZIPS, "set_ids": [4], "types": ["terrain", "maison"],
                 "prix_max": 400000, "target": 110, "pages": 5, "min_terrain_maison": 300},
}


def search_zips(source, zips, types, prix_max, pages, limit=100):
    """Recherche Leboncoin sur plusieurs CP en une requête paginée (tri par récence)."""
    ret = sorted({_APP_TO_RET[t] for t in types if t in _APP_TO_RET})
    out = []
    for page in range(1, pages + 1):
        payload = {
            "filters": {
                "category": {"id": "9"},
                "enums": {"ad_type": ["offer"], "real_estate_type": ret},
                "ranges": {"price": {"max": int(prix_max)}},
                "location": {"city_zipcodes": [{"zipcode": z} for z in zips]},
            },
            "limit": limit, "offset": (page - 1) * limit,
            "sort_by": "time", "sort_order": "desc",
        }
        resp = source._post("/finder/search", json_body=payload, headers=source._headers())
        ads = resp.json().get("ads") or []
        if not ads:
            break
        out.extend(annotate(source._normalize(ad)) for ad in ads)
        if len(ads) < limit:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="petit lot Pauline")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    init_db()
    print("Seed DB depuis data.json...", flush=True)
    recap = seed_from_data_json()
    print(f"  -> {recap}", flush=True)

    source = LeboncoinSource()
    zones = {"pauline": dict(ZONES["pauline"], pages=1, target=10)} if args.test else ZONES

    # 1) Collecte (dédupe par external_id, et retire ceux déjà en base)
    db = SessionLocal()
    existing = {e for (e,) in db.query(Listing.external_id).filter(Listing.source == "leboncoin").all()}
    collected = {}  # external_id -> (item, set_ids)
    for name, z in zones.items():
        pages = z.get("pages", 3)
        items = search_zips(source, z["zips"], z["types"], z["prix_max"], pages)
        kept = 0
        for it in items:
            if not it.external_id or it.external_id in existing or it.external_id in collected:
                continue
            if it.prix is None or it.prix > z["prix_max"]:
                continue
            # Certains sets (Ploemeur) ne veulent des maisons QUE si elles ont du terrain.
            min_terr = z.get("min_terrain_maison")
            if min_terr and it.type_bien == "maison" and (it.surface_terrain or 0) < min_terr:
                continue
            # viagers conservés (notés très bas à l'export, cf. export_static)
            collected[it.external_id] = (it, z["set_ids"])
            kept += 1
            if kept >= z["target"]:
                break
        print(f"[{name}] {kept} biens neufs (sur {len(items)} annonces)", flush=True)

    todo = list(collected.values())
    print(f"\nEnrichissement de {len(todo)} biens ({args.workers} workers)...", flush=True)

    # 2) Enrichissement parallèle (I/O-bound)
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

    # 3) Upsert + set_ids
    for item, set_ids in enriched:
        row = upsert_listing(db, item)
        row.set_ids = set_ids
    db.commit()
    n_lbc = db.query(Listing).filter(Listing.source == "leboncoin").count()
    print(f"\nEn base : {n_lbc} biens leboncoin.", flush=True)

    # 4) Export data.json + photos (sauf en mode test)
    if not args.test:
        from app.services.export_static import export_to_dir
        import os
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        print(f"\nExport vers {os.path.abspath(data_dir)} (photos incluses)...", flush=True)
        t1 = time.time()
        stats = export_to_dir(db, data_dir, download_photos=True)
        print(f"  export OK en {time.time()-t1:.0f}s : {stats}", flush=True)
    db.close()
    print("TERMINÉ.", flush=True)


if __name__ == "__main__":
    sys.exit(main())
