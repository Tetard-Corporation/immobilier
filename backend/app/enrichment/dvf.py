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
    """Médiane des prix au m² à partir de couples (prix, surface), aberrations filtrées."""
    valeurs = [
        p / s
        for p, s in pairs
        if isinstance(p, (int, float)) and isinstance(s, (int, float)) and p > 0 and s and s > 0
    ]
    valeurs = [v for v in valeurs if 1 <= v <= 50000]
    if len(valeurs) < 3:
        return None
    return round(median(valeurs), 1)


@functools.lru_cache(maxsize=256)
def _commune_rows(base_url: str, year: str, dept: str, insee: str, timeout: int) -> tuple:
    """Mutations 'Vente' d'une commune : tuple de (prix, surface_bati, surface_terrain, lat, lon)."""
    url = f"{base_url}/{year}/communes/{dept}/{insee}.csv"
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError:
        return ()
    if resp.status_code != 200:
        return ()

    def _f(v):
        try:
            return float(v or 0)
        except ValueError:
            return 0.0

    rows = []
    for rec in csv.DictReader(io.StringIO(resp.text)):
        if rec.get("nature_mutation") != "Vente":
            continue
        try:
            vf = float(rec["valeur_fonciere"])
        except (KeyError, ValueError, TypeError):
            continue
        rows.append((vf, _f(rec.get("surface_reelle_bati")), _f(rec.get("surface_terrain")),
                     _f(rec.get("latitude")), _f(rec.get("longitude"))))
    return tuple(rows)


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
        # Ventes proches du point (~1,5 km) en priorité, sinon toute la commune.
        near = [r for r in rows if r[3] and r[4] and haversine_km(lat, lon, r[3], r[4]) <= 1.5]
        used = near if len(near) >= 5 else rows
        # Comparables séparés bâti vs terrain (on ne mélange pas les deux marchés).
        bati = [(vf, sb) for (vf, sb, st, _, _) in used if sb > 0]
        terrain = [(vf, st) for (vf, sb, st, _, _) in used if sb == 0 and st > 0]
        out = {}
        m2_bati = prix_m2_median(bati)
        m2_terrain = prix_m2_median(terrain)
        if m2_bati is not None:
            out["prix_m2_secteur_bati"] = m2_bati
        if m2_terrain is not None:
            out["prix_m2_secteur_terrain"] = m2_terrain
        return out
