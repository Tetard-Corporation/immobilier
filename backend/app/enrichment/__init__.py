"""Registre des providers d'enrichissement et application au pipeline."""

from __future__ import annotations

from .base import EnrichmentProvider
from .dvf import DvfComparablesProvider
from .georisques import GeorisquesProvider
from .gpu import GpuZonageProvider
from .pollution import PollutionProvider
from .rail import RailTimeProvider
from .relief import ReliefProvider
from .socio import SocioProvider

_registry: list[EnrichmentProvider] | None = None


def get_providers() -> list[EnrichmentProvider]:
    global _registry
    if _registry is None:
        _registry = [
            GpuZonageProvider(),
            GeorisquesProvider(),
            ReliefProvider(),
            PollutionProvider(),
            SocioProvider(),
            RailTimeProvider(),
            DvfComparablesProvider(),
        ]
    return _registry


def reset_providers(providers: list[EnrichmentProvider] | None = None) -> None:
    """Réinitialise / injecte des providers (tests)."""
    global _registry
    _registry = providers


def provider_status() -> list[dict]:
    return [{"name": p.name, "available": p.available} for p in get_providers()]


def enrich_listing(item):
    """Enrichit un bien géolocalisé via les providers disponibles, recalcule le score."""
    if item.latitude is None or item.longitude is None:
        return item
    flags = dict(item.flags or {})
    for provider in get_providers():
        if not provider.available:
            continue
        flags.update(provider.enrich(item.latitude, item.longitude))

    # Écart de prix vs secteur : on compare bâti à bâti, terrain à terrain.
    if item.type_bien == "terrain":
        secteur, surface = flags.get("prix_m2_secteur_terrain"), item.surface_terrain
    else:
        secteur, surface = flags.get("prix_m2_secteur_bati"), item.surface_bati
    if secteur and item.prix and surface:
        flags["prix_m2_secteur"] = secteur
        flags["ecart_prix_pct"] = round((item.prix / surface - secteur) / secteur * 100, 1)

    # Recalcule le score d'investissement avec les nouvelles composantes (constructible,
    # risques, PEB, comparables...) maintenant disponibles.
    from ..services.scoring import compute_score

    ctx = {
        "has_text": bool(item.description or item.adresse),
        "surface_terrain": item.surface_terrain,
        "type_bien": item.type_bien,
    }
    result = compute_score(flags, ctx)
    flags["score"] = result.score
    flags["score_details"] = result.pillars

    item.flags = flags
    return item
