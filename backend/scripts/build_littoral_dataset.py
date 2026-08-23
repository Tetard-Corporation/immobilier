#!/usr/bin/env python3
"""Construit `data/littoral_bretagne_sud.json` : les points de côte **ouverte** de
Bretagne sud, qui servent au critère « proximité du littoral » (`near_sea`).

Pourquoi filtrer la côte au lieu de la prendre telle quelle : OpenStreetMap étiquette
`natural=coastline` jusqu'à la limite des marées, donc les rias (Blavet jusqu'à
Hennebont, Scorff jusqu'à Pont-Scorff, Laïta jusqu'à Quimperlé, rivière d'Étel) sont
du « littoral » au sens OSM. Sans filtrage, Pont-Scorff ressort à 900 m « de la mer »
alors qu'il est à 11 km de l'océan — et le critère « plutôt côté mer » ne veut plus
rien dire.

Le filtre retenu, l'**enveloppe maritime** : la côte ouverte est, pour une longitude
donnée, la côte la plus au sud. On découpe donc l'emprise en tranches de longitude et
on ne garde que ce qui se trouve à moins de `--marge` km de la latitude minimale de la
tranche. Les rias, orientées nord-sud, sont éliminées au-delà de leur embouchure. Les
îles (chaînes fermées : Groix…) sont conservées entières : toute leur côte est ouverte.

Usage :
    python scripts/build_littoral_dataset.py
    python scripts/build_littoral_dataset.py --bbox 47.55,-3.95,47.95,-3.05 --pas 0.5
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict

_OVERPASS = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
# Overpass renvoie 406 sur les User-Agent en "Mozilla/5.0 (compatible; ...)" : jeton simple.
_UA = "immobilier-littoral/1.0"
_OUT = os.path.join(os.path.dirname(__file__), "..", "data", "littoral_bretagne_sud.json")


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi, dlmb = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def fetch_coastline(bbox: tuple[float, float, float, float], timeout: int) -> list[list[tuple[float, float]]]:
    """Récupère les tronçons `natural=coastline` de l'emprise (Overpass)."""
    query = f'[out:json][timeout:{timeout}];way["natural"="coastline"]({",".join(str(v) for v in bbox)});out geom;'
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(_OVERPASS, data=data, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout + 60) as resp:
        payload = json.load(resp)
    ways = []
    for el in payload.get("elements", []):
        geom = [(round(p["lat"], 7), round(p["lon"], 7)) for p in (el.get("geometry") or [])]
        if len(geom) >= 2:
            ways.append(geom)
    return ways


def chain_ways(ways: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Recolle les tronçons bout à bout : OSM découpe la côte continue en centaines de
    ways, alors qu'on a besoin de lignes continues pour distinguer îles et continent."""
    par_debut: dict[tuple[float, float], list[int]] = defaultdict(list)
    for i, w in enumerate(ways):
        par_debut[w[0]].append(i)
    utilise = [False] * len(ways)
    chaines = []
    for i, w in enumerate(ways):
        if utilise[i]:
            continue
        utilise[i] = True
        chaine = list(w)
        while True:
            suivants = [j for j in par_debut.get(chaine[-1], []) if not utilise[j]]
            if not suivants:
                break
            j = suivants[0]
            utilise[j] = True
            chaine.extend(ways[j][1:])
        chaines.append(chaine)
    return chaines


def resample(chaine: list[tuple[float, float]], pas_km: float) -> list[tuple[float, float]]:
    """Ré-échantillonne une ligne à un pas régulier (précision finale ≈ pas/2)."""
    out = [chaine[0]]
    dernier = chaine[0]
    for p in chaine[1:]:
        if _haversine_km(dernier, p) >= pas_km:
            out.append(p)
            dernier = p
    return out


def enveloppe_maritime(continent: list[tuple[float, float]], bin_deg: float, marge_km: float) -> list[tuple[float, float]]:
    """Ne garde, par tranche de longitude, que la côte la plus au sud (± marge)."""
    tranches: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for p in continent:
        tranches[round(p[1] / bin_deg)].append(p)
    garde = []
    for pts in tranches.values():
        lat_min = min(p[0] for p in pts)
        # 1° de latitude ≈ 111 km : conversion suffisante à cette échelle.
        garde += [p for p in pts if (p[0] - lat_min) * 111.0 <= marge_km]
    return garde


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", default="47.55,-3.95,47.95,-3.05", help="sud,ouest,nord,est")
    ap.add_argument("--pas", type=float, default=0.5, help="pas d'échantillonnage (km)")
    ap.add_argument("--bin", type=float, default=0.010, help="largeur des tranches de longitude (degrés)")
    ap.add_argument("--marge", type=float, default=2.0, help="tolérance sous la côte la plus au sud (km)")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--out", default=_OUT)
    args = ap.parse_args()

    bbox = tuple(float(v) for v in args.bbox.split(","))
    print(f"Overpass : côte de l'emprise {bbox}...", flush=True)
    ways = fetch_coastline(bbox, args.timeout)
    print(f"  {len(ways)} tronçons", flush=True)

    chaines = chain_ways(ways)
    continent: list[tuple[float, float]] = []
    iles: list[tuple[float, float]] = []
    for c in chaines:
        # Une chaîne fermée (début == fin) est une île : toute sa côte est ouverte.
        (iles if c[0] == c[-1] else continent).extend(resample(c, args.pas))
    print(f"  {len(chaines)} chaînes -> continent {len(continent)} pts, îles {len(iles)} pts", flush=True)

    points = enveloppe_maritime(continent, args.bin, args.marge) + iles
    points = sorted({(round(a, 4), round(b, 4)) for a, b in points})
    print(f"  côte ouverte retenue : {len(points)} points", flush=True)

    doc = (
        "Littoral OUVERT de Bretagne sud (points lat/lon), échantillonné tous les "
        f"~{int(args.pas * 1000)} m depuis OpenStreetMap (natural=coastline), emprise "
        f"{args.bbox}. Les rias (Blavet/Scorff au-dessus de la rade de Lorient, Laïta "
        "au-dessus de son embouchure, rivière d'Étel) sont exclues par l'enveloppe "
        "maritime, sinon Hennebont ou Pont-Scorff passeraient pour du bord de mer ; "
        "les îles (Groix…) sont conservées entières. Sert au critère near_sea "
        "(distance au point de côte le plus proche, précision ~250 m). "
        "Régénérer : python scripts/build_littoral_dataset.py"
    )
    out = os.path.abspath(args.out)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"_doc": doc, "points": [[a, b] for a, b in points]}, fh, ensure_ascii=False)
    print(f"Écrit : {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
