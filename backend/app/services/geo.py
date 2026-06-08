"""Utilitaires géographiques : distances, corridor (polyligne), villes connues."""

from __future__ import annotations

import math

# Coordonnées (lat, lon) de villes courantes — sert au parseur de brief
# (corridor "Paris-Marseille", trajet "depuis Paris", etc.).
CITY_COORDS: dict[str, tuple[float, float]] = {
    "paris": (48.8566, 2.3522),
    "marseille": (43.2965, 5.3698),
    "lyon": (45.7640, 4.8357),
    "bordeaux": (44.8378, -0.5792),
    "toulouse": (43.6045, 1.4440),
    "nantes": (47.2184, -1.5536),
    "montpellier": (43.6108, 3.8767),
    "lille": (50.6292, 3.0573),
    "nice": (43.7102, 7.2620),
    "strasbourg": (48.5734, 7.7521),
    "rennes": (48.1173, -1.6778),
    "grenoble": (45.1885, 5.7245),
    "dijon": (47.3220, 5.0415),
    "clermont-ferrand": (45.7772, 3.0870),
    "valence": (44.9334, 4.8924),
    "avignon": (43.9493, 4.8055),
    "nimes": (43.8367, 4.3601),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance en km entre deux points (formule de haversine)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _point_segment_km(lat: float, lon: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance approx. d'un point au segment [a,b] (projection en plan local)."""
    # Projection équirectangulaire centrée sur le point (suffisant aux échelles régionales).
    def to_xy(p):
        return (
            math.radians(p[1] - lon) * math.cos(math.radians(lat)) * 6371.0,
            math.radians(p[0] - lat) * 6371.0,
        )

    ax, ay = to_xy(a)
    bx, by = to_xy(b)
    # point est l'origine (0,0)
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0:
        return math.hypot(ax, ay)
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(px, py)


def distance_to_corridor_km(lat: float, lon: float, points: list[tuple[float, float]]) -> float | None:
    """Distance minimale d'un point à une polyligne (liste de (lat,lon))."""
    pts = [(p[0], p[1]) for p in points if isinstance(p, (list, tuple)) and len(p) == 2]
    if len(pts) < 2:
        if len(pts) == 1:
            return haversine_km(lat, lon, pts[0][0], pts[0][1])
        return None
    return min(_point_segment_km(lat, lon, pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def resolve_city(name: str | None) -> tuple[float, float] | None:
    if not name:
        return None
    return CITY_COORDS.get(name.strip().lower())


_GEOCODE_CACHE: dict[str, dict | None] = {}


def _ban_municipality(q: str) -> dict | None:
    import httpx

    r = httpx.get(
        "https://api-adresse.data.gouv.fr/search/",
        params={"q": q, "type": "municipality", "limit": 1},
        timeout=12,
    )
    r.raise_for_status()
    feats = (r.json() or {}).get("features") or []
    if not feats:
        return None
    f = feats[0]
    lon, lat = f["geometry"]["coordinates"]
    p = f.get("properties") or {}
    insee = p.get("citycode")
    return {
        "nom": p.get("name"), "score": p.get("score", 0.0),
        "lat": lat, "lon": lon,
        "code_postal": p.get("postcode"),
        "code_commune": insee,
        "departement": (insee or "")[:2] or None,
    }


def geocode_locality(label: str | None) -> dict | None:
    """Résout une commune depuis une étiquette libre (titre d'annonce d'agence,
    nom de commune…) via la BAN. La commune étant en fin d'étiquette, on essaie les
    fenêtres de tokens finales (1..4 mots) et on garde le meilleur score (léger biais
    aux fenêtres plus longues = plus spécifiques). Renvoie {nom, lat, lon,
    code_postal, code_commune, departement} ou None. Mis en cache."""
    if not label or not label.strip():
        return None
    key = label.strip().lower()
    if key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[key]
    import re

    clean = re.sub(r"[^a-zA-ZÀ-ÿ'\- ]", " ", label).lower()
    clean = re.sub(r"\bst\b", "saint", clean)
    clean = re.sub(r"\bste\b", "sainte", clean)
    toks = clean.split()
    best = None
    for k in (5, 4, 3, 2, 1):
        if k > len(toks):
            continue
        try:
            res = _ban_municipality(" ".join(toks[-k:]))
        except Exception:
            res = None
        if res and res["nom"]:
            rank = res["score"] + 0.06 * k  # privilégie les fenêtres plus longues (plus spécifiques)
            if best is None or rank > best[0]:
                best = (rank, res)
    out = best[1] if best and best[1]["score"] >= 0.4 else None
    _GEOCODE_CACHE[key] = out
    return out


# Hubs TGV (gare, temps TGV en minutes depuis Paris) pour estimer un porte-à-porte :
# on prend le hub minimisant (TGV + voiture jusqu'au bien).
RAIL_HUBS = {
    "Lyon": ((45.7605, 4.8597), 115),
    "Valence TGV": ((44.9920, 4.9786), 130),
    "Avignon TGV": ((43.9214, 4.7860), 160),
    "Marseille": ((43.3027, 5.3804), 190),
    "Grenoble": ((45.1916, 5.7144), 180),
    "Dijon": ((47.3220, 5.0415), 95),
    "Aix-en-Provence TGV": ((43.4553, 5.3174), 180),
    "Nîmes": ((43.8326, 4.3650), 175),
    "Montpellier": ((43.6108, 3.8767), 195),
}
_CAR_KMH = 65  # vitesse routière moyenne (départementales de montagne)
_CAR_OVERHEAD_MIN = 12  # accès gare/voiture, marges


def porte_a_porte_min(lat: float, lon: float, hubs: dict | None = None) -> int | None:
    """Temps de trajet estimé porte-à-porte depuis Paris (min) : meilleur hub TGV + voiture."""
    if lat is None or lon is None:
        return None
    hubs = hubs or RAIL_HUBS
    best = None
    for (hlat, hlon), tgv_min in hubs.values():
        car_min = haversine_km(hlat, hlon, lat, lon) / _CAR_KMH * 60 + _CAR_OVERHEAD_MIN
        total = tgv_min + car_min
        if best is None or total < best:
            best = total
    return round(best) if best is not None else None
