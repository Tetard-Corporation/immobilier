"""CRUD des recherches fréquentes + exécution, résultats et nouveautés."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Listing, SavedSearch, SearchRun, SeenListing
from ..schemas import ListingOut, SavedSearchIn, SavedSearchOut, SearchRunOut
from ..services.alerts import count_new, mark_all_seen, run_saved_search

router = APIRouter(prefix="/saved-searches", tags=["saved-searches"])


def _get_or_404(db: Session, ss_id: int) -> SavedSearch:
    ss = db.get(SavedSearch, ss_id)
    if ss is None:
        raise HTTPException(status_code=404, detail="Recherche introuvable.")
    return ss


def _serialize(db: Session, ss: SavedSearch) -> SavedSearchOut:
    out = SavedSearchOut.model_validate(ss)
    out.nb_new = count_new(db, ss.id)
    return out


def _listing_to_out(row: Listing, is_new: bool | None = None) -> ListingOut:
    out = ListingOut.model_validate(row)
    if row.prix and row.surface_terrain:
        out.prix_m2_terrain = round(row.prix / row.surface_terrain, 2)
    out.is_new = is_new
    return out


@router.get("", response_model=list[SavedSearchOut])
def list_saved_searches(db: Session = Depends(get_db)) -> list[SavedSearchOut]:
    rows = db.execute(select(SavedSearch).order_by(SavedSearch.updated_at.desc())).scalars()
    return [_serialize(db, ss) for ss in rows]


@router.post("", response_model=SavedSearchOut, status_code=201)
def create_saved_search(payload: SavedSearchIn, db: Session = Depends(get_db)) -> SavedSearchOut:
    if payload.criteria is None and payload.filter_set_id is None:
        raise HTTPException(
            status_code=422, detail="Fournir 'criteria' ou 'filter_set_id'."
        )
    ss = SavedSearch(
        name=payload.name,
        source=payload.source,
        criteria=payload.criteria.model_dump(exclude_none=True) if payload.criteria else {},
        filter_set_id=payload.filter_set_id,
        frequency_minutes=payload.frequency_minutes,
        enabled=payload.enabled,
    )
    db.add(ss)
    db.commit()
    db.refresh(ss)
    return _serialize(db, ss)


@router.get("/{ss_id}", response_model=SavedSearchOut)
def get_saved_search(ss_id: int, db: Session = Depends(get_db)) -> SavedSearchOut:
    return _serialize(db, _get_or_404(db, ss_id))


@router.put("/{ss_id}", response_model=SavedSearchOut)
def update_saved_search(
    ss_id: int, payload: SavedSearchIn, db: Session = Depends(get_db)
) -> SavedSearchOut:
    ss = _get_or_404(db, ss_id)
    ss.name = payload.name
    ss.source = payload.source
    if payload.criteria is not None:
        ss.criteria = payload.criteria.model_dump(exclude_none=True)
    ss.filter_set_id = payload.filter_set_id
    ss.frequency_minutes = payload.frequency_minutes
    ss.enabled = payload.enabled
    db.commit()
    db.refresh(ss)
    return _serialize(db, ss)


@router.delete("/{ss_id}", status_code=204, response_class=Response)
def delete_saved_search(ss_id: int, db: Session = Depends(get_db)) -> Response:
    ss = _get_or_404(db, ss_id)
    db.delete(ss)
    db.commit()
    return Response(status_code=204)


@router.post("/{ss_id}/run", response_model=SearchRunOut)
def run_now(ss_id: int, db: Session = Depends(get_db)) -> SearchRun:
    ss = _get_or_404(db, ss_id)
    run = run_saved_search(db, ss)
    if run.error:
        raise HTTPException(status_code=409, detail=run.error)
    return run


@router.get("/{ss_id}/results", response_model=list[ListingOut])
def saved_search_results(
    ss_id: int,
    only_new: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> list[ListingOut]:
    _get_or_404(db, ss_id)
    stmt = (
        select(SeenListing, Listing)
        .join(Listing, SeenListing.listing_id == Listing.id)
        .where(SeenListing.saved_search_id == ss_id)
        .order_by(SeenListing.first_seen_at.desc())
    )
    if only_new:
        stmt = stmt.where(SeenListing.is_new.is_(True))
    return [_listing_to_out(listing, is_new=seen.is_new) for seen, listing in db.execute(stmt)]


@router.get("/{ss_id}/runs", response_model=list[SearchRunOut])
def saved_search_runs(ss_id: int, db: Session = Depends(get_db)) -> list[SearchRun]:
    _get_or_404(db, ss_id)
    return list(
        db.execute(
            select(SearchRun)
            .where(SearchRun.saved_search_id == ss_id)
            .order_by(SearchRun.ran_at.desc())
        ).scalars()
    )


@router.post("/{ss_id}/mark-seen")
def mark_seen(ss_id: int, db: Session = Depends(get_db)) -> dict:
    _get_or_404(db, ss_id)
    updated = mark_all_seen(db, ss_id)
    return {"updated": updated}
