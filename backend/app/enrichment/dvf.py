"""Provider comparables DVF via l'open data **geo-dvf** (gratuit, sans clé).

Source : files.data.gouv.fr/geo-dvf — CSV des mutations par commune. On résout la
commune (reverse-geocoding BAN), on télécharge le CSV de la commune (mis en cache),
on calcule un prix au m² de secteur (médiane, ventes proches du point en priorité),
puis l'écart du bien vs marché (`ecart_prix_pct`) qui alimente le pilier « Prix ».

Aucune dépendance à Pappers (API payante) pour ce besoin.
"""

from __future__ import annotations

import csv
import functools
import io
from statistics import median

import httpx

from ..services.geo import haversine_km
from .base import EnrichmentProvider


def prix_m2_median(pairs: list[tuple[float | None, float | None]]) -> float | None:
    """Médiane des prix au m² à partir de couples (prix, surface), aberrations filtrées.

    Au-delà de 8 ventes on écrête les déciles extrêmes : le DVF contient des mutations
    aberrantes (micro-parcelles, cessions à prix symbolique, lots mal ventilés) qui
    déportent la médiane d'un facteur 10 quand l'échantillon est petit.
    """
    valeurs = [
        p / s
        for p, s in pairs
        if isinstance(p, (int, float)) and isinstance(s, (int, float)) and p > 0 and s and s > 0
    ]
    valeurs = sorted(v for v in valeurs if 1 <= v <= 50000)
    if len(valeurs) < 3:
        return None
    if len(valeurs) >= 8:
        lo = int(len(valeurs) * 0.10)
        valeurs = valeurs[lo:len(valeurs) - lo]
    return round(median(valeurs), 1)


# Nb minimal de ventes dans le rayon de 1,5 km pour préférer le voisinage à la commune.
_MIN_COMPARABLES_PROCHES = 12


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# Natures de culture du marché FONCIER BÂTISSABLE. Le reste (terres, prés, landes, bois,
# vergers…) est du foncier agricole/naturel, qui se vend à quelques €/m² : mélanger les
# deux dans une même médiane produit une référence qui ne décrit aucun des deux marchés.
_NATURES_URBAINES = {"", "sols", "terrains a batir", "terrains à bâtir", "terrains a bâtir",
                     "jardins", "terrains d'agrement", "terrains d'agrément"}


def _est_urbain(natures: set[str]) -> bool:
    return bool(natures) and natures <= _NATURES_URBAINES


@functools.lru_cache(maxsize=256)
def _commune_rows(base_url: str, year: str, dept: str, insee: str, timeout: int) -> tuple:
    """Ventes d'une commune, agrégées PAR MUTATION : (prix, bâti, terrain, lat, lon, urbain).

    Une mutation DVF s'étale sur autant de lignes que de parcelles/lots, en RÉPÉTANT
    `valeur_fonciere` sur chacune. Compter les lignes revient donc à compter le prix
    total plusieurs fois, chaque fois divisé par une seule parcelle : c'est ce qui
    faisait ressortir des « prix de secteur » à 2 500 €/m² sur des communes à 270 €/m².
    On regroupe donc par `id_mutation` : prix de la mutation, surfaces cumulées.
    """
    url = f"{base_url}/{year}/communes/{dept}/{insee}.csv"
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError:
        return ()
    if resp.status_code != 200:
        return ()

    mutations: dict[str, dict] = {}
    for rec in csv.DictReader(io.StringIO(resp.text)):
        if rec.get("nature_mutation") != "Vente":
            continue
        try:
            vf = float(rec["valeur_fonciere"])
        except (KeyError, ValueError, TypeError):
            continue
        key = rec.get("id_mutation") or f"_{len(mutations)}"
        m = mutations.setdefault(key, {"vf": 0.0, "sb": 0.0, "st": 0.0, "lat": 0.0, "lon": 0.0, "nat": set()})
        m["vf"] = max(m["vf"], vf)  # valeur répétée à l'identique sur chaque ligne
        m["sb"] += _f(rec.get("surface_reelle_bati"))
        m["st"] += _f(rec.get("surface_terrain"))
        m["nat"].add((rec.get("nature_culture") or "").strip().lower())
        if not m["lat"]:
            m["lat"], m["lon"] = _f(rec.get("latitude")), _f(rec.get("longitude"))

    return tuple(
        (m["vf"], m["sb"], m["st"], m["lat"], m["lon"], _est_urbain(m["nat"]))
        for m in mutations.values()
    )


class DvfComparablesProvider(EnrichmentProvider):
    name = "dvf_comparables"

    def __init__(self, settings=None, client=None) -> None:
        super().__init__(settings, client)
        s = self._settings
        self._base = s.dvf_base_url
        self._years = [y.strip() for y in s.dvf_years.split(",") if y.strip()]

    def _fetch(self, lat: float, lon: float) -> dict:
        insee = self._reverse_citycode(lat, lon)
        if not insee:
            return {}
        dept = insee[:3] if insee.startswith("97") else insee[:2]
        rows: tuple = ()
        for year in self._years:
            rows = _commune_rows(self._base, year, dept, insee, self._settings.http_timeout_seconds)
            if rows:
                break
        if not rows:
            return {}
        # Comparables séparés bâti vs terrain (on ne mélange pas les deux marchés), et
        # pour le terrain nu, seul le foncier bâtissable (un terrain à bâtir ne se compare
        # pas à un pré).
        def _bati(pool):
            return [(vf, sb) for (vf, sb, st, _, _, _) in pool if sb > 0]

        def _terrain(pool):
            return [(vf, st) for (vf, sb, st, _, _, urb) in pool if sb == 0 and st > 0 and urb]

        # Ventes proches du point (~1,5 km) si CE marché-là y est assez représenté, sinon
        # toute la commune. Le choix se fait marché par marché : une commune peut avoir
        # 20 ventes de maisons dans le rayon et 2 de terrain nu, auquel cas le terrain doit
        # basculer sur la commune plutôt que se passer de référence. Le seuil est
        # volontairement haut : à 5 ventes, deux biens voisins tiraient deux « marchés »
        # différents (jusqu'à 14 références distinctes dans une même commune).
        near = [r for r in rows if r[3] and r[4] and haversine_km(lat, lon, r[3], r[4]) <= 1.5]

        def _reference(extract):
            proches = extract(near)
            if len(proches) >= _MIN_COMPARABLES_PROCHES:
                return prix_m2_median(proches)
            return prix_m2_median(extract(rows))

        out = {}
        m2_bati = _reference(_bati)
        m2_terrain = _reference(_terrain)
        if m2_bati is not None:
            out["prix_m2_secteur_bati"] = m2_bati
        if m2_terrain is not None:
            out["prix_m2_secteur_terrain"] = m2_terrain
        return out
