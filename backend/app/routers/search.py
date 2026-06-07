"""Endpoint : recherche ad hoc."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sqlalchemy import select

from ..db import get_db
from ..models import SearchHistory
from ..schemas import SearchCriteria, SearchHistoryOut, SearchResultOut
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
    filter_set_id: int | None = Query(default=None, description="Set de filtres à l'origine (historique)."),
    db: Session = Depends(get_db),
) -> SearchResultOut:
    try:
        return run_search(
            db, source, criteria, dedupe_results=dedupe, sort_by_score=(sort == "score"),
            enrich=enrich, filter_set_id=filter_set_id,
        )
    except RuntimeError as exc:  # ex. ScraperBlocked (anti-bot)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:  # source inconnue
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except httpx.HTTPError as exc:  # erreur réseau côté source
        raise HTTPException(status_code=502, detail=f"Source indisponible : {exc}") from exc


@router.get("/search-history", response_model=list[SearchHistoryOut])
def search_history(
    limit: int = Query(default=50, ge=1, le=500),
    filter_set_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[SearchHistory]:
    """Historique systématique de toutes les recherches lancées (plus récentes d'abord)."""
    stmt = select(SearchHistory).order_by(SearchHistory.ran_at.desc()).limit(limit)
    if filter_set_id is not None:
        stmt = stmt.where(SearchHistory.filter_set_id == filter_set_id)
    return list(db.execute(stmt).scalars())
