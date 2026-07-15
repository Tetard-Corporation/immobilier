"""Re-enrichit les risques Géorisques (filtrés au niveau adresse) pour tous les biens,
en parallèle, et met à jour Listing.risques. À lancer avant un re-export."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.db import SessionLocal
from app.enrichment.georisques import GeorisquesProvider
from app.models import Listing

WORKERS = 4


def main():
    db = SessionLocal()
    rows = [r for r in db.query(Listing).filter(Listing.source != "mock").all()
            if r.latitude is not None and r.longitude is not None]
    print(f"{len(rows)} biens à re-enrichir (risques)...", flush=True)
    prov = GeorisquesProvider()

    def work(r):
        for attempt in range(3):
            try:
                return r.id, prov.enrich(r.latitude, r.longitude).get("risques")
            except Exception:  # noqa: BLE001
                time.sleep(1.5 * (attempt + 1))
        return r.id, None

    t0 = time.time()
    results = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(work, r) for r in rows]
        for i, f in enumerate(as_completed(futs), 1):
            rid, risques = f.result()
            if risques is not None:
                results[rid] = risques
            if i % 50 == 0:
                print(f"  {i}/{len(rows)} ({time.time()-t0:.0f}s)", flush=True)

    by_id = {r.id: r for r in rows}
    changed = 0
    for rid, risques in results.items():
        row = by_id[rid]
        if (row.risques or []) != risques:
            row.risques = risques
            changed += 1
    db.commit()
    print(f"OK : {len(results)}/{len(rows)} re-enrichis, {changed} modifiés, en {time.time()-t0:.0f}s", flush=True)
    # aperçu
    ex_row = next((r for r in rows if r.commune == "Morlaix"), None)
    if ex_row:
        print("ex Morlaix risques ->", ex_row.risques)


if __name__ == "__main__":
    main()
