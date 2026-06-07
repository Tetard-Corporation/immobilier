"""Communes françaises avec centroïdes (pour des recherches par rayon exhaustives).

Permet d'énumérer toutes les communes dans un rayon autour d'un point — contrairement
à une recherche par département (plafonnée aux 100 annonces les plus récentes), on cible
de petites zones (peu d'annonces chacune → aucune troncature), puis on étend le rayon.

Les centroïdes viennent de geo.api.gouv.fr et sont mis en cache sur disque par département.
"""

from __future__ import annotations

import json
import os
import urllib.request

from .geo import haversine_km

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "communes")
_API = "https://geo.api.gouv.fr/departements/{dep}/communes?fields=nom,code,centre&format=json"


def _cache_path(dep: str) -> str:
    return os.path.join(_CACHE_DIR, f"{dep}.json")


def load_departement(dep: str) -> list[dict]:
    """Communes d'un département : [{nom, code, lat, lon}]. Cache disque + réseau au besoin."""
    path = _cache_path(dep)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        pass
    try:
        req = urllib.request.Request(_API.format(dep=dep), headers={"User-Agent": "immobilier"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = json.loads(r.read())
    except Exception:
        return []
    out = []
    for c in raw:
        centre = c.get("centre") or {}
        coords = centre.get("coordinates")
        if not coords:
            continue
        out.append({"nom": c.get("nom"), "code": c.get("code"), "lat": coords[1], "lon": coords[0]})
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False)
    except Exception:
        pass
    return out


def communes_within(lat: float, lon: float, radius_km: float, depts: list[str]) -> list[dict]:
    """Communes (des départements donnés) dont le centroïde tombe dans le rayon, triées par distance."""
    found = []
    for dep in depts:
        for c in load_departement(dep):
            d = haversine_km(lat, lon, c["lat"], c["lon"])
            if d <= radius_km:
                found.append({**c, "dist_km": round(d, 1)})
    found.sort(key=lambda c: c["dist_km"])
    return found
