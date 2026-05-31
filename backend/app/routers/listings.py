"""Endpoint : détail d'un bien/parcelle (enrichissement à la demande)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Listing
from ..schemas import ListingOut
from ..services.search import to_listing_out, upsert_listing
from ..sources import resolve_source

router = APIRouter(prefix="/listings", tags=["listings"])


@router.get("/{listing_id}", response_model=ListingOut)
def get_listing(listing_id: int, db: Session = Depends(get_db)) -> ListingOut:
    row = db.get(Listing, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Bien introuvable.")
    out = ListingOut.model_validate(row)
    if row.prix and row.surface_terrain:
        out.prix_m2_terrain = round(row.prix / row.surface_terrain, 2)
    return out


@router.post("/{listing_id}/enrich", response_model=ListingOut)
def enrich_listing(
    listing_id: int,
    bases: str | None = Query(default=None, description="Bases Pappers, séparées par des virgules."),
    db: Session = Depends(get_db),
) -> ListingOut:
    """Recharge le détail depuis la source (bases supplémentaires : ventes, dpe, proprietaires...)."""
    row = db.get(Listing, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Bien introuvable.")
    source = resolve_source(row.source)
    base_list = [b.strip() for b in bases.split(",")] if bases else None
    try:
        item = source.get(row.external_id, bases=base_list)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Bien absent de la source.")
    updated = upsert_listing(db, item)
    db.commit()
    return to_listing_out(item, db_id=updated.id)
