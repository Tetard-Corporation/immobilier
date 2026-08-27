"""Réchauffe en PARALLÈLE les caches Overpass (poi/infra) + télécharge les photos,
pour que l'export qui suit ne fasse que des cache/disk hits (rapide).
Ne touche pas à la DB (lecture seule)."""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.db import SessionLocal
from app.models import Listing
from app.services import export_static as E

# Overpass n'accorde que ~2 slots d'exécution par IP. Au-delà, il répond 406/429 et
# — comme _query_poi/_query_overpass avalent l'exception — les résultats sont perdus
# EN SILENCE. Vécu : 1046 requêtes à 4 workers, zéro entrée de cache en plus.
WORKERS = int(os.environ.get("WARM_WORKERS", "2"))
# Nb de tentatives par point avant d'abandonner (back-off progressif entre chaque).
TRIES = int(os.environ.get("WARM_TRIES", "3"))
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")


def _key(r):
    return f"{round(r.latitude, 4)},{round(r.longitude, 4)}"


def _write_atomic(path: str, payload: dict) -> None:
    """Écrit via un fichier temporaire + rename : un kill en pleine écriture ne
    laisse pas un cache JSON tronqué (donc illisible au prochain export)."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)


def _flush(poi: dict, infra: dict) -> None:
    _write_atomic(E._POI_CACHE, poi)
    _write_atomic(E._INFRA_CACHE, infra)


def main():
    ok, message = E.verifier_overpass()
    print(f"Instance Overpass — {message}", flush=True)
    if not ok:
        print("ABANDON : réchauffer sur cette instance remplirait le cache de zéros "
              "indiscernables de vrais zéros. Choisir une instance qui couvre la France "
              "(OVERPASS_URL=https://overpass.openstreetmap.fr/api/interpreter).", flush=True)
        return 1

    db = SessionLocal()
    rows = [r for r in db.query(Listing).filter(Listing.source != "mock").all()
            if r.latitude is not None and r.longitude is not None]
    poi = E._load_poi_cache()
    infra = E._load_infra_cache()
    os.makedirs(PHOTOS_DIR, exist_ok=True)

    need_poi = {_key(r): (r.latitude, r.longitude) for r in rows if _key(r) not in poi}
    need_infra = {_key(r): (r.latitude, r.longitude) for r in rows if _key(r) not in infra}
    print(f"{len(rows)} biens | POI à requêter: {len(need_poi)} | INFRA: {len(need_infra)}", flush=True)

    def _retry(fn, lat, lon):
        """Overpass renvoie 429/406 quand ses slots sont pris : on réessaie avec
        back-off au lieu de perdre le point."""
        for attempt in range(TRIES):
            res = fn(lat, lon)
            if res is not None:
                return res
            if attempt < TRIES - 1:
                time.sleep(5 * (attempt + 1))
        return None

    def q_poi(item):
        k, (lat, lon) = item
        return ("poi", k, _retry(E._query_poi, lat, lon))

    def q_infra(item):
        k, (lat, lon) = item
        return ("infra", k, _retry(E._query_overpass, lat, lon))

    t0 = time.time()
    tasks = [(q_poi, it) for it in need_poi.items()] + [(q_infra, it) for it in need_infra.items()]
    done = 0
    failed = {"poi": 0, "infra": 0}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(fn, it) for fn, it in tasks]
        for f in as_completed(futs):
            typ, k, res = f.result()
            if res is not None:
                (poi if typ == "poi" else infra)[k] = res
            else:
                failed[typ] += 1
            done += 1
            if done % 25 == 0:
                # Flush périodique : Overpass met ~20 min pour un gros lot, et un
                # réchauffage interrompu ne doit pas repartir de zéro (vécu : 300
                # requêtes perdues parce que l'écriture n'avait lieu qu'à la fin).
                _flush(poi, infra)
                print(f"  overpass {done}/{len(tasks)} ({time.time()-t0:.0f}s, caches écrits)", flush=True)
    _flush(poi, infra)
    print(f"caches écrits (poi {len(poi)}, infra {len(infra)}) en {time.time()-t0:.0f}s", flush=True)
    if failed["poi"] or failed["infra"]:
        # Ne PAS laisser un run à rendement nul passer pour un succès.
        print(f"⚠ {failed['poi']} POI et {failed['infra']} INFRA abandonnés après "
              f"{TRIES} tentatives (Overpass saturé). Relancer warm.py : les points "
              f"déjà en cache ne sont pas re-demandés.", flush=True)

    # Photos en parallèle (skip viagers : ils seront exclus de l'export)
    photo_rows = [r for r in rows if not E._detect_viager(r.description, r.adresse)]
    print(f"\nPhotos : {len(photo_rows)} biens ({WORKERS} workers)...", flush=True)
    t1 = time.time()
    done = 0
    failed = {"poi": 0, "infra": 0}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(E._download_photos, r, PHOTOS_DIR, "photos") for r in photo_rows]
        for f in as_completed(futs):
            try:
                f.result()
            except Exception:
                pass
            done += 1
            if done % 40 == 0:
                print(f"  photos {done}/{len(photo_rows)} ({time.time()-t1:.0f}s)", flush=True)
    print(f"photos OK en {time.time()-t1:.0f}s", flush=True)
    print("WARM TERMINÉ.", flush=True)


if __name__ == "__main__":
    main()
