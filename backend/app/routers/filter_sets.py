"""CRUD des jeux de filtres réutilisables."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import FilterSet
from ..schemas import FilterSetIn, FilterSetOut

router = APIRouter(prefix="/filter-sets", tags=["filter-sets"])


def _get_or_404(db: Session, fs_id: int) -> FilterSet:
    fs = db.get(FilterSet, fs_id)
    if fs is None:
        raise HTTPException(status_code=404, detail="Jeu de filtres introuvable.")
    return fs


@router.get("", response_model=list[FilterSetOut])
def list_filter_sets(db: Session = Depends(get_db)) -> list[FilterSet]:
    return list(db.execute(select(FilterSet).order_by(FilterSet.updated_at.desc())).scalars())


@router.post("", response_model=FilterSetOut, status_code=201)
def create_filter_set(payload: FilterSetIn, db: Session = Depends(get_db)) -> FilterSet:
    fs = FilterSet(
        name=payload.name,
        description=payload.description,
        criteria=payload.criteria.model_dump(exclude_none=True),
    )
    db.add(fs)
    db.commit()
    db.refresh(fs)
    return fs


@router.get("/{fs_id}", response_model=FilterSetOut)
def get_filter_set(fs_id: int, db: Session = Depends(get_db)) -> FilterSet:
    return _get_or_404(db, fs_id)


@router.put("/{fs_id}", response_model=FilterSetOut)
def update_filter_set(fs_id: int, payload: FilterSetIn, db: Session = Depends(get_db)) -> FilterSet:
    fs = _get_or_404(db, fs_id)
    fs.name = payload.name
    fs.description = payload.description
    fs.criteria = payload.criteria.model_dump(exclude_none=True)
    db.commit()
    db.refresh(fs)
    return fs


@router.delete("/{fs_id}", status_code=204, response_class=Response)
def delete_filter_set(fs_id: int, db: Session = Depends(get_db)) -> Response:
    fs = _get_or_404(db, fs_id)
    db.delete(fs)
    db.commit()
    return Response(status_code=204)
