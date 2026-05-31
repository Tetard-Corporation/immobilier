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
    """Mutations 'Vente' d'une commune : tuple de (prix, surface, lat, lon). Caché."""
    url = f"{base_url}/{year}/communes/{dept}/{insee}.csv"
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError:
        return ()
    if resp.status_code != 200:
        return ()
    rows = []
    for rec in csv.DictReader(io.StringIO(resp.text)):
        if rec.get("nature_mutation") != "Vente":
            continue
        try:
            vf = float(rec["valeur_fonciere"])
        except (KeyError, ValueError, TypeError):
            continue
        surface = 0.0
        for col in ("surface_terrain", "surface_reelle_bati"):
            try:
                surface = float(rec.get(col) or 0) or surface
            except ValueError:
                pass
        try:
            lat = float(rec.get("latitude") or 0)
            lon = float(rec.get("longitude") or 0)
        except ValueError:
            lat = lon = 0.0
        rows.append((vf, surface, lat, lon))
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
        # Privilégie les ventes proches du point (~1,5 km) ; sinon toute la commune.
        near = [(vf, s) for (vf, s, la, lo) in rows if la and lo and haversine_km(lat, lon, la, lo) <= 1.5]
        pairs = near if len(near) >= 5 else [(vf, s) for (vf, s, _, _) in rows]
        m2 = prix_m2_median(pairs)
        return {"prix_m2_secteur": m2} if m2 is not None else {}
