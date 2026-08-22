"""Résout Listing.code_commune (INSEE) hors-ligne depuis data/communes/<dep>.json
({nom, code, lat, lon}). Match par nom dans le département (via code_postal), repli
sur le centroïde le plus proche. Corrige les code_commune manquants (biens re-seedés)
et erronés (leboncoin mettait city_label). Clé de la fibre (LUT INSEE)."""
from __future__ import annotations

import glob
import json
import os
import re
import unicodedata
from math import cos, radians

from app.db import SessionLocal
from app.models import Listing

HERE = os.path.dirname(__file__)


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _load():
    by_dep, allc = {}, []
    for path in glob.glob(os.path.join(HERE, "data", "communes", "*.json")):
        dep = os.path.basename(path)[:2]
        for c in json.load(open(path, encoding="utf-8")):
            rec = {"code": c["code"], "lat": c["lat"], "lon": c["lon"], "nom": _norm(c["nom"])}
            by_dep.setdefault(dep, []).append(rec)
            allc.append(rec)
    return by_dep, allc


def _nearest(lat, lon, pool):
    if lat is None or lon is None or not pool:
        return None
    k = cos(radians(lat))
    best = min(pool, key=lambda c: (c["lat"] - lat) ** 2 + ((c["lon"] - lon) * k) ** 2)
    return best["code"]


def main():
    by_dep, allc = _load()
    print(f"communes chargées : {len(allc)} ({', '.join(sorted(by_dep))})", flush=True)
    db = SessionLocal()
    rows = db.query(Listing).filter(Listing.source != "mock").all()
    n_name = n_geo = n_none = 0
    for r in rows:
        dep = (r.code_postal or "")[:2]
        pool = by_dep.get(dep, [])
        code = None
        if pool and r.commune:
            nom = _norm(r.commune)
            hit = next((c for c in pool if c["nom"] == nom), None)
            if hit:
                code = hit["code"]; n_name += 1
        if code is None:
            code = _nearest(r.latitude, r.longitude, pool or allc)
            if code:
                n_geo += 1
        if code:
            r.code_commune = code
        else:
            n_none += 1
    db.commit()
    print(f"résolu : {n_name} par nom, {n_geo} par géo, {n_none} échecs / {len(rows)}", flush=True)
    # contrôle : combien matchent la LUT fibre ?
    from app.services.export_static import _load_fibre_lut
    lut = _load_fibre_lut()
    hit = sum(1 for r in rows if r.code_commune in lut)
    print(f"code_commune présent dans la LUT fibre : {hit}/{len(rows)}", flush=True)


if __name__ == "__main__":
    main()
