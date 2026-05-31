"""Moteur de préférences pondérées (régime ranking).

Aucune préférence n'exclut un bien : chacune produit un sous-score [0,1] (ou None si
non applicable / donnée manquante), agrégé en `match_score` [0,100] qui sert à classer.
Les préférences dépendant d'un enrichissement non encore branché (trajet train, fibre,
relief, rando) renvoient un statut `pending` tant que la donnée n'est pas disponible.
"""

from __future__ import annotations

from .gares import nearest_gare
from .geo import distance_to_corridor_km, haversine_km, resolve_city

# Préférences évaluables dès maintenant (annonce + géo) ; le reste = pending.
PREFERENCE_KINDS = [
    "budget",
    "chambres_min",
    "has_terrain",
    "light_works",
    "no_vis_a_vis",
    "nature_exception",
    "authentic",
    "feature",
    "near_corridor",
    "near_gare",
    "near_city",
    "population_jeune",
    "orientation_gauche",
    # Dépendent d'un provider d'enrichissement (Lot A) :
    "rail_time_from",
    "fiber",
    "relief_mountain",
    "hiking",
]

_PENDING_KINDS = {"rail_time_from", "fiber", "relief_mountain", "hiking"}
_LIGHT_OK = {"habitable": 1.0, "rafraichir": 1.0, "renover": 0.85, "gros_travaux": 0.4, "ruine": 0.1}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _corridor_points(params: dict) -> list[tuple[float, float]]:
    pts = [tuple(p) for p in params.get("points", []) if isinstance(p, (list, tuple)) and len(p) == 2]
    for city in params.get("villes", []) or params.get("cities", []):
        c = resolve_city(city)
        if c:
            pts.append(c)
    return pts


def _eval_one(item, kind: str, params: dict):
    """Renvoie (subscore|None, status, detail_str)."""
    flags = item.flags or {}

    if kind == "budget":
        if item.prix is None:
            return None, "n/a", "prix inconnu"
        budget = params.get("budget_max") or (params.get("apport", 0) * params.get("levier", 4))
        if not budget:
            return None, "n/a", "budget non défini"
        if item.prix <= budget:
            return 1.0, "ok", f"≤ budget {int(budget)}€"
        return _clamp(1 - (item.prix - budget) / budget), "ok", f"> budget {int(budget)}€"

    if kind == "chambres_min":
        mn = params.get("min", 1)
        if item.nb_chambres is None:
            return None, "n/a", "nb chambres inconnu"
        return (1.0 if item.nb_chambres >= mn else _clamp(item.nb_chambres / mn)), "ok", f"{item.nb_chambres} ch."

    if kind == "has_terrain":
        if item.surface_terrain is None:
            return None, "n/a", "surface terrain inconnue"
        mn = params.get("min_surface", 1)
        return (1.0 if item.surface_terrain >= mn else _clamp(item.surface_terrain / mn)), "ok", f"{item.surface_terrain} m²"

    if kind == "light_works":
        cond = flags.get("condition")
        if cond is None:
            return None, "n/a", "état inconnu"
        return _LIGHT_OK.get(cond, 0.6), "ok", cond

    if kind == "no_vis_a_vis":
        if "sans_vis_a_vis" in (flags.get("features") or []):
            return 1.0, "ok", "sans vis-à-vis"
        if "vis_a_vis" in (flags.get("nuisances") or []):
            return 0.0, "ok", "vis-à-vis signalé"
        return 0.6, "ok", "non précisé"

    if kind == "nature_exception":
        return (1.0 if flags.get("nature_exception") else _clamp(0.4 + 0.1 * (flags.get("nature_score") or 0))), "ok", ""

    if kind == "authentic":
        return (1.0 if "authentique" in (flags.get("features") or []) else 0.3), "ok", ""

    if kind == "feature":
        name = params.get("name")
        return (1.0 if name in (flags.get("features") or []) else 0.2), "ok", str(name)

    if kind == "near_corridor":
        if item.latitude is None or item.longitude is None:
            return None, "n/a", "géoloc manquante"
        pts = _corridor_points(params)
        dist = distance_to_corridor_km(item.latitude, item.longitude, pts)
        if dist is None:
            return None, "n/a", "corridor non défini"
        max_km = params.get("max_km", 40)
        return _clamp(1 - dist / max_km), "ok", f"{round(dist)} km de l'axe"

    if kind == "near_gare":
        if item.latitude is None or item.longitude is None:
            return None, "n/a", "géoloc manquante"
        res = nearest_gare(item.latitude, item.longitude)
        if res is None:
            return None, "n/a", "données gares indispo"
        _, dist = res
        max_km = params.get("max_km", 10)
        return _clamp(1 - dist / max_km), "ok", f"gare à {dist} km"

    if kind == "near_city":
        if item.latitude is None or item.longitude is None:
            return None, "n/a", "géoloc manquante"
        center = resolve_city(params.get("ville")) or (params.get("lat"), params.get("lon"))
        if not center or center[0] is None:
            return None, "n/a", "ville non résolue"
        dist = haversine_km(item.latitude, item.longitude, center[0], center[1])
        return _clamp(1 - dist / params.get("max_km", 50)), "ok", f"{round(dist)} km"

    if kind == "population_jeune":
        v = flags.get("pop_jeune_score")
        if v is None:
            return None, "pending", "données socio (enrich)"
        age = flags.get("age_median")
        return _clamp(v), "ok", f"âge médian {age}" if age else "population jeune"

    if kind == "orientation_gauche":
        v = flags.get("orientation_gauche_score")
        if v is None:
            return None, "pending", "données socio (enrich)"
        return _clamp(v), "ok", f"part gauche {round(v * 100)}%"

    if kind in _PENDING_KINDS:
        # Lit le champ d'enrichissement s'il existe déjà ; sinon pending.
        key = {"rail_time_from": "rail_time_min", "fiber": "fibre", "relief_mountain": "altitude", "hiking": "randonnee"}[kind]
        if key not in flags:
            return None, "pending", "provider non branché"
        val = flags[key]
        if kind == "rail_time_from":
            return _clamp(1 - val / params.get("max_minutes", 180)), "ok", f"{val} min"
        if kind == "fiber":
            return (1.0 if val else 0.0), "ok", "fibre" if val else "pas de fibre"
        if kind == "relief_mountain":
            return _clamp((val or 0) / params.get("ref_altitude", 600)), "ok", f"{val} m"
        if kind == "hiking":
            return (1.0 if val else 0.3), "ok", ""

    return None, "n/a", "inconnu"


def evaluate(item, preferences) -> tuple[float | None, list[dict]]:
    """Calcule le match_score (0-100) et le détail par préférence."""
    if not preferences:
        return None, []
    details = []
    total_w = 0.0
    acc = 0.0
    for pref in preferences:
        kind = getattr(pref, "kind", None) or (pref.get("kind") if isinstance(pref, dict) else None)
        weight = getattr(pref, "weight", None) if not isinstance(pref, dict) else pref.get("weight", 1.0)
        weight = 1.0 if weight is None else float(weight)
        params = getattr(pref, "params", None) if not isinstance(pref, dict) else pref.get("params", {})
        params = params or {}
        label = getattr(pref, "label", None) if not isinstance(pref, dict) else pref.get("label")

        sub, status, detail = _eval_one(item, kind, params)
        entry = {
            "kind": kind,
            "label": label or kind,
            "weight": weight,
            "status": status,
            "detail": detail,
            "subscore": round(sub, 3) if sub is not None else None,
        }
        if sub is not None and status == "ok":
            total_w += weight
            acc += weight * sub
            entry["contribution"] = round((weight * sub), 3)
        details.append(entry)

    if total_w == 0:
        return None, details
    score = round(acc / total_w * 100, 1)
    details.sort(key=lambda d: d.get("contribution", -1), reverse=True)
    return score, details
