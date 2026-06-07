"""Provider GPU (Géoportail de l'Urbanisme) : zonage PLU d'un point (sans clé).

typezone : U (urbain, constructible), AU (à urbaniser → bientôt constructible),
A (agricole), N (naturel). Renseigne constructible / est_zone_au pour le score et
les préférences.
"""

from __future__ import annotations

import json

from .base import EnrichmentProvider


class GpuZonageProvider(EnrichmentProvider):
    name = "gpu_zonage"

    def _fetch(self, lat: float, lon: float) -> dict:
        geom = json.dumps({"type": "Point", "coordinates": [lon, lat]})
        resp = self._get_client().get(
            f"{self._settings.gpu_api_url}/zone-urba", params={"geom": geom}
        )
        resp.raise_for_status()
        features = resp.json().get("features") or []
        if not features:
            return {}
        props = features[0].get("properties") or {}
        typezone = props.get("typezone")
        return {
            "zone_urba": typezone,
            "zone_libelle": props.get("libelle"),
            "constructible": typezone == "U",
            "est_zone_au": typezone == "AU",
        }
