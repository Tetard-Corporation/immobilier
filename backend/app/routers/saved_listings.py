"""Favoris / historique des biens sauvegardés."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Listing, SavedListing
from ..schemas import ListingOut, SavedListingIn, SavedListingOut

router = APIRouter(prefix="/saved-listings", tags=["saved-listings"])


def _snapshot_from_listing(row: Listing) -> dict:
    out = ListingOut.model_validate(row)
    if row.prix and row.surface_terrain:
        out.prix_m2_terrain = round(row.prix / row.surface_terrain, 2)
    return out.model_dump()


@router.get("", response_model=list[SavedListingOut])
def list_saved(
    filter_set_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[SavedListing]:
    stmt = select(SavedListing).order_by(SavedListing.saved_at.desc())
    if filter_set_id is not None:
        stmt = stmt.where(SavedListing.filter_set_id == filter_set_id)
    return list(db.execute(stmt).scalars())


@router.post("", response_model=SavedListingOut, status_code=201)
def save_listing(payload: SavedListingIn, db: Session = Depends(get_db)) -> SavedListing:
    """Sauvegarde un bien (par listing_id, ou par source+external_id)."""
    row: Listing | None = None
    if payload.listing_id is not None:
        row = db.get(Listing, payload.listing_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Bien introuvable.")
    elif payload.source and payload.external_id:
        row = db.execute(
            select(Listing).where(
                Listing.source == payload.source, Listing.external_id == payload.external_id
            )
        ).scalar_one_or_none()
    else:
        raise HTTPException(status_code=422, detail="Fournir listing_id ou (source, external_id).")

    source = payload.source or (row.source if row else None)
    external_id = payload.external_id or (row.external_id if row else None)
    if not source or not external_id:
        raise HTTPException(status_code=422, detail="source/external_id requis.")

    existing = db.execute(
        select(SavedListing).where(
            SavedListing.source == source, SavedListing.external_id == external_id
        )
    ).scalar_one_or_none()
    snapshot = payload.snapshot or (_snapshot_from_listing(row) if row else {})
    if existing:  # idempotent : on met à jour la note / le rattachement / le snapshot
        if payload.note is not None:
            existing.note = payload.note
        if payload.filter_set_id is not None:
            existing.filter_set_id = payload.filter_set_id
        if snapshot:
            existing.snapshot = snapshot
        db.commit()
        db.refresh(existing)
        return existing

    saved = SavedListing(
        listing_id=row.id if row else None,
        source=source,
        external_id=external_id,
        filter_set_id=payload.filter_set_id,
        note=payload.note,
        snapshot=snapshot,
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)
    return saved


@router.get("/{saved_id}", response_model=SavedListingOut)
def get_saved(saved_id: int, db: Session = Depends(get_db)) -> SavedListing:
    saved = db.get(SavedListing, saved_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Favori introuvable.")
    return saved


@router.delete("/{saved_id}", status_code=204, response_class=Response)
def delete_saved(saved_id: int, db: Session = Depends(get_db)) -> Response:
    saved = db.get(SavedListing, saved_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Favori introuvable.")
    db.delete(saved)
    db.commit()
    return Response(status_code=204)
