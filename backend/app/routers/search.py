"""Endpoint : recherche ad hoc."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import SearchCriteria, SearchResultOut
from ..services.brief import parse_brief
from ..services.search import run_search

router = APIRouter(tags=["search"])


class BriefIn(BaseModel):
    text: str


@router.post("/brief/parse")
def brief_parse(payload: BriefIn) -> dict:
    """Convertit un brief en langage naturel en préférences pondérées éditables."""
    return parse_brief(payload.text)


@router.post("/search", response_model=SearchResultOut)
def search(
    criteria: SearchCriteria,
    source: str | None = Query(default=None, description="Nom de source ('auto' par défaut)."),
    dedupe: bool = Query(default=False, description="Fusionner les biens en double."),
    sort: str | None = Query(default=None, description="'score' pour trier par score décroissant."),
    enrich: bool = Query(default=False, description="Enrichir (zonage, risques, relief, train)."),
    db: Session = Depends(get_db),
) -> SearchResultOut:
    try:
        return run_search(
            db, source, criteria, dedupe_results=dedupe, sort_by_score=(sort == "score"), enrich=enrich
        )
    except RuntimeError as exc:  # ex. ScraperBlocked (anti-bot)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:  # source inconnue
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except httpx.HTTPError as exc:  # erreur réseau côté source
        raise HTTPException(status_code=502, detail=f"Source indisponible : {exc}") from exc
