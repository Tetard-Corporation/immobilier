"""Provider fibre : taux d'éligibilité FTTH de la commune (Arcep open data).

Index compact `data/fibre_communes.json` : {code_insee: % de locaux éligibles FTTH},
dérivé de « Ma connexion internet » (Arcep). Reverse-geocoding BAN pour la commune.
"""

from __future__ import annotations

import json
import os

from .base import EnrichmentProvider

_LUT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "fibre_communes.json")


def _load_lut() -> dict:
    try:
        with open(_LUT_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


class FibreProvider(EnrichmentProvider):
    name = "fibre"
    _lut: dict | None = None

    @property
    def available(self) -> bool:
        if FibreProvider._lut is None:
            FibreProvider._lut = _load_lut()
        return bool(FibreProvider._lut)

    def _fetch(self, lat: float, lon: float) -> dict:
        if FibreProvider._lut is None:
            FibreProvider._lut = _load_lut()
        code = self._reverse_citycode(lat, lon)
        if not code or code not in FibreProvider._lut:
            return {}
        pct = FibreProvider._lut[code]
        return {"fibre": pct >= 50, "fibre_pct": pct}
