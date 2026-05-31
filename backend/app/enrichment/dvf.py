"""Provider comparables DVF via l'API Pappers Immobilier (clé requise).

Calcule un prix au m² de secteur à partir des ventes récentes autour du bien, puis
l'écart du bien vs ce marché (`ecart_prix_pct`) — qui alimente la composante « affaire »
du score. Indisponible (et donc sans effet) tant que PAPPERS_API_KEY n'est pas configuré.
"""

from __future__ import annotations

from statistics import median

from .base import EnrichmentProvider


def prix_m2_median(pairs: list[tuple[float | None, float | None]]) -> float | None:
    """Médiane des prix au m² à partir de couples (prix, surface), valeurs aberrantes filtrées."""
    valeurs = [
        p / s
        for p, s in pairs
        if isinstance(p, (int, float)) and isinstance(s, (int, float)) and p > 0 and s and s > 0
    ]
    # Filtre grossier des aberrations (€/m² hors plage plausible).
    valeurs = [v for v in valeurs if 1 <= v <= 50000]
    if len(valeurs) < 3:
        return None
    return round(median(valeurs), 1)


class DvfComparablesProvider(EnrichmentProvider):
    name = "dvf_comparables"

    def __init__(self, settings=None, client=None) -> None:
        super().__init__(settings, client)
        self._pappers = None

    @property
    def available(self) -> bool:
        return self._settings.pappers_configured

    def _source(self):
        if self._pappers is None:
            from ..sources.pappers import PappersSource

            self._pappers = PappersSource(self._settings)
        return self._pappers

    def _fetch(self, lat: float, lon: float) -> dict:
        from ..schemas import SearchCriteria

        crit = SearchCriteria(
            latitude=lat, longitude=lon, distance=600, bases=["ventes"], par_page=50
        )
        result = self._source().search(crit)
        pairs = [(it.prix, (it.surface_terrain or it.surface_bati)) for it in result.items]
        m2 = prix_m2_median(pairs)
        return {"prix_m2_secteur": m2} if m2 is not None else {}
