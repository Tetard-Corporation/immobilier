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


# --- Résolution par code postal ---------------------------------------------- #
# Un code postal peut couvrir plusieurs communes (et une commune avoir plusieurs
# codes). Certaines sources ne donnent qu'un code postal (SeLoger, agences) : il faut
# alors le nom de commune canonique pour construire une URL ou géolocaliser le bien.
_API_CP = "https://geo.api.gouv.fr/communes?codePostal={cp}&fields=nom,code,centre,population&format=json"
_CP_CACHE = os.path.join(_CACHE_DIR, "par_code_postal.json")


def _load_cp_cache() -> dict:
    try:
        with open(_CP_CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def communes_for_postcode(code_postal: str) -> list[dict]:
    """Communes d'un code postal : [{nom, code, lat, lon}]. Cache disque + réseau."""
    cp = str(code_postal).strip().zfill(5)
    cache = _load_cp_cache()
    if cp in cache:
        return cache[cp]
    try:
        req = urllib.request.Request(_API_CP.format(cp=cp), headers={"User-Agent": "immobilier"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = json.loads(r.read())
    except Exception:
        return []  # panne transitoire : ne pas mémoriser un échec
    out = []
    for c in raw:
        coords = (c.get("centre") or {}).get("coordinates")
        out.append({"nom": c.get("nom"), "code": c.get("code"),
                    "lat": coords[1] if coords else None,
                    "lon": coords[0] if coords else None,
                    "population": c.get("population") or 0})
    out.sort(key=lambda c: -c["population"])  # chef-lieu du code postal en tête
    cache[cp] = out
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_CP_CACHE, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, sort_keys=True)
    except Exception:
        pass
    return out


def main_commune_for_postcode(code_postal: str) -> dict | None:
    """Commune la plus peuplée d'un code postal (son « chef-lieu » de fait).

    Un code postal rural en couvre parfois vingt (26110 = Nyons + 22 villages). Les
    portails qui n'indexent qu'à la commune (SeLoger) obligent à choisir : on prend la
    plus peuplée, où se concentrent les annonces."""
    communes = communes_for_postcode(code_postal)
    return communes[0] if communes else None


def _sans_accents(txt: str) -> str:
    import unicodedata
    base = unicodedata.normalize("NFD", (txt or "").lower())
    return "".join(c for c in base if unicodedata.category(c) != "Mn").replace("-", " ").strip()


def code_insee(commune: str | None, code_postal: str | None) -> str | None:
    """Code INSEE d'une commune depuis son NOM et son code postal.

    Nécessaire parce que tous les portails ne donnent pas le code : Leboncoin renvoie un
    libellé (« Chalencon 07240 ») là où le modèle attend un code INSEE. Sans lui, la
    fibre ne se résout pas (elle est indexée par code INSEE) et le dédoublonnage
    inter-sources échoue — mesuré : 1 430 biens sur 1 580.

    Le nom seul ne suffit pas (des dizaines de communes s'appellent Saint-Martin), le
    code postal seul non plus (un CP rural couvre jusqu'à vingt communes) : il faut les
    deux, et on retombe sur la plus peuplée du CP si le nom ne correspond à aucune.
    """
    if not code_postal:
        return None
    candidates = communes_for_postcode(code_postal)
    if not candidates:
        return None
    cible = _sans_accents(commune or "")
    for c in candidates:
        if _sans_accents(c.get("nom")) == cible:
            return c.get("code")
    return candidates[0].get("code") if cible else None
