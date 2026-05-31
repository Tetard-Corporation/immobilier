"""Registre des providers d'enrichissement et application au pipeline."""

from __future__ import annotations

from .base import EnrichmentProvider
from .georisques import GeorisquesProvider
from .gpu import GpuZonageProvider
from .rail import RailTimeProvider
from .relief import ReliefProvider

_registry: list[EnrichmentProvider] | None = None


def get_providers() -> list[EnrichmentProvider]:
    global _registry
    if _registry is None:
        _registry = [GpuZonageProvider(), GeorisquesProvider(), ReliefProvider(), RailTimeProvider()]
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

    # Recalcule le score d'investissement avec les nouvelles composantes (constructible,
    # risques, PEB, comparables...) maintenant disponibles.
    from ..services.scoring import compute_score

    has_text = bool(item.description or item.adresse)
    result = compute_score(flags, has_text=has_text)
    flags["score"] = result.score
    flags["score_details"] = result.components

    item.flags = flags
    return item
