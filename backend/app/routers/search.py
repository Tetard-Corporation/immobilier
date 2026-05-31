"""Endpoint : recherche ad hoc."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import SearchCriteria, SearchResultOut
from ..services.search import run_search

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResultOut)
def search(
    criteria: SearchCriteria,
    source: str | None = Query(default=None, description="Nom de source ('auto' par défaut)."),
    db: Session = Depends(get_db),
) -> SearchResultOut:
    try:
        return run_search(db, source, criteria)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
