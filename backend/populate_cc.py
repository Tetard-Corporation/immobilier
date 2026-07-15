"""Re-peuple Listing.code_commune (INSEE) via BAN reverse pour les biens qui l'ont
perdu (re-seed depuis data.json qui ne l'exportait pas). Parallèle, fiable."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from app.db import SessionLocal
from app.models import Listing


def _insee(lat, lon):
    for _ in range(3):
        try:
            r = httpx.get("https://api-adresse.data.gouv.fr/reverse/",
                          params={"lat": lat, "lon": lon, "type": "municipality"}, timeout=15)
            r.raise_for_status()
            feats = r.json().get("features") or []
            if feats:
                return feats[0]["properties"].get("citycode")
            return None
        except Exception:
            time.sleep(1.0)
    return None


def main():
    db = SessionLocal()
    rows = [r for r in db.query(Listing).filter(Listing.source != "mock").all()
            if not r.code_commune and r.latitude is not None and r.longitude is not None]
    print(f"{len(rows)} biens sans code_commune -> BAN reverse...", flush=True)
    t0 = time.time()
    res = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_insee, r.latitude, r.longitude): r.id for r in rows}
        for i, f in enumerate(as_completed(futs), 1):
            res[futs[f]] = f.result()
            if i % 50 == 0:
                print(f"  {i}/{len(rows)} ({time.time()-t0:.0f}s)", flush=True)
    by_id = {r.id: r for r in rows}
    n = 0
    for rid, cc in res.items():
        if cc:
            by_id[rid].code_commune = cc
            n += 1
    db.commit()
    print(f"OK : {n}/{len(rows)} code_commune renseignés en {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
