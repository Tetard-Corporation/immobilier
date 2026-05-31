"""Registre des sources de données disponibles."""

from __future__ import annotations

from .base import ListingSource, NormalizedListing, SearchResult
from .bienici import BienIciSource
from .leboncoin import LeboncoinSource
from .mock import MockSource
from .pap import PapSource
from .pappers import PappersSource
from .seloger import SeLogerSource

_registry: dict[str, ListingSource] | None = None


def _build_registry() -> dict[str, ListingSource]:
    return {
        "pappers": PappersSource(),
        "bienici": BienIciSource(),
        "leboncoin": LeboncoinSource(),
        "pap": PapSource(),
        "seloger": SeLogerSource(),
        "mock": MockSource(),
    }


def get_registry() -> dict[str, ListingSource]:
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry


def reset_registry(registry: dict[str, ListingSource] | None = None) -> None:
    """Réinitialise le registre (tests, ou injection d'une source factice)."""
    global _registry
    _registry = registry


def default_source_name() -> str:
    """Source par défaut : Pappers si configurée, sinon le mock."""
    reg = get_registry()
    pappers = reg.get("pappers")
    if pappers is not None and pappers.available:
        return "pappers"
    return "mock"


def resolve_source(name: str | None) -> ListingSource:
    """Résout un nom de source ('auto'/None -> défaut) en instance utilisable."""
    reg = get_registry()
    if not name or name == "auto":
        name = default_source_name()
    source = reg.get(name)
    if source is None:
        raise KeyError(f"Source inconnue : {name}")
    return source


__all__ = [
    "ListingSource",
    "NormalizedListing",
    "SearchResult",
    "get_registry",
    "reset_registry",
    "default_source_name",
    "resolve_source",
]
