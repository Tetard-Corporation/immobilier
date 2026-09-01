"""Réchauffe en PARALLÈLE les caches Overpass (poi/infra) et relief (IGN) + télécharge
les photos, pour que l'export qui suit ne fasse que des cache/disk hits (rapide).
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
# Le relief vient de l'API altimétrique de l'IGN, pas d'Overpass : elle n'a pas la limite
# de 2 slots, mais elle est LENTE (~11 s pour les 9 points d'une couronne). Laisser
# l'export l'interroger en série est le piège qui l'a fait tourner une heure sans rien
# écrire : 431 points manquants × 11 s, et un `data.json` écrit seulement à la fin.
RELIEF_WORKERS = int(os.environ.get("WARM_RELIEF_WORKERS", "6"))
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


def _warm_relief(rows) -> None:
    """Proéminence IGN des points encore absents du cache, en parallèle."""
    cache = E._load_relief_cache()
    besoin = {_key(r): (r.latitude, r.longitude) for r in rows if _key(r) not in cache}
    print(f"\nRelief (IGN) : {len(besoin)} points à requêter "
          f"({RELIEF_WORKERS} workers)...", flush=True)
    if not besoin:
        return
    t0 = time.time()
    done = abandonnes = 0

    def q(item):
        k, (lat, lon) = item
        return k, _retry_simple(E._query_prominence, lat, lon)

    with ThreadPoolExecutor(max_workers=RELIEF_WORKERS) as ex:
        for f in as_completed([ex.submit(q, it) for it in besoin.items()]):
            k, res = f.result()
            if res is not None:
                cache[k] = res
            else:
                abandonnes += 1
            done += 1
            if done % 50 == 0:
                _write_atomic(E._RELIEF_CACHE, cache)
                print(f"  relief {done}/{len(besoin)} ({time.time()-t0:.0f}s, cache écrit)",
                      flush=True)
    _write_atomic(E._RELIEF_CACHE, cache)
    print(f"relief écrit ({len(cache)} points) en {time.time()-t0:.0f}s", flush=True)
    if abandonnes:
        print(f"⚠ {abandonnes} points de relief abandonnés (IGN injoignable). "
              f"Relancer warm.py : les points en cache ne sont pas re-demandés.", flush=True)


def _retry_simple(fn, lat, lon, tries: int = 3):
    for attempt in range(tries):
        res = fn(lat, lon)
        if res is not None:
            return res
        if attempt < tries - 1:
            time.sleep(2 * (attempt + 1))
    return None


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

    _warm_relief(rows)

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
