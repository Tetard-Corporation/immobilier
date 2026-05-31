"""Endpoints : sources disponibles et schéma des filtres."""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas import SourceInfo
from ..services.filters import get_filter_schema
from ..sources import default_source_name, get_registry

router = APIRouter(tags=["meta"])

_NOTES = {
    "pappers": "API officielle Pappers Immobilier. Nécessite PAPPERS_API_KEY.",
    "mock": "Jeu de données de démonstration (aucune clé requise).",
}


@router.get("/sources", response_model=list[SourceInfo])
def list_sources() -> list[SourceInfo]:
    default = default_source_name()
    infos: list[SourceInfo] = []
    for name, source in get_registry().items():
        infos.append(
            SourceInfo(
                name=name,
                label=source.label,
                available=source.available,
                is_default=(name == default),
                note=_NOTES.get(name),
            )
        )
    return infos


@router.get("/filters/schema")
def filters_schema() -> dict:
    return get_filter_schema()
