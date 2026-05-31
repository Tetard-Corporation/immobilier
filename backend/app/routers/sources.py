"""Endpoints : sources disponibles et schéma des filtres."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import SourceInfo
from ..services.agences_ingest import ingest as ingest_agences
from ..services.filters import get_filter_schema
from ..sources import default_source_name, get_registry

router = APIRouter(tags=["meta"])

_NOTES = {
    "pappers": "API officielle Pappers Immobilier. Nécessite PAPPERS_API_KEY.",
    "bienici": "Annonces Bien'ici (scraping API JSON). Géo fine filtrée côté client.",
    "leboncoin": "Annonces Leboncoin (API finder). Protégé Datadome : requiert PROXY_URL.",
    "pap": "Annonces PAP (de particulier à particulier). Cloudflare : requiert headless/proxy.",
    "seloger": "Annonces SeLoger. Protégé Datadome : requiert headless/proxy.",
    "paruvendu": "Annonces Paruvendu (HTML rendu serveur, accessible sans proxy).",
    "agences": "Newsletters d'agences (IMAP) + sites d'agences. Ingestion inbound.",
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


@router.get("/enrichment/status")
def enrichment_status() -> list[dict]:
    """État des providers d'enrichissement (zonage, risques, relief, trajet train)."""
    from ..enrichment import provider_status

    return provider_status()


@router.post("/agences/ingest")
def trigger_agences_ingest(db: Session = Depends(get_db)) -> dict:
    """Déclenche manuellement l'ingestion des newsletters/sites d'agences."""
    return ingest_agences(db)
