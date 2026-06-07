"""CRUD des jeux de filtres réutilisables."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import FilterSet
from ..schemas import FilterSetIn, FilterSetOut, FilterSetResolvedOut
from ..services.filtersets import resolve_criteria

router = APIRouter(prefix="/filter-sets", tags=["filter-sets"])


def _get_or_404(db: Session, fs_id: int) -> FilterSet:
    fs = db.get(FilterSet, fs_id)
    if fs is None:
        raise HTTPException(status_code=404, detail="Jeu de filtres introuvable.")
    return fs


def _check_parent(db: Session, parent_id: int | None, self_id: int | None = None) -> None:
    if parent_id is None:
        return
    if parent_id == self_id:
        raise HTTPException(status_code=422, detail="Un set ne peut pas être son propre parent.")
    if db.get(FilterSet, parent_id) is None:
        raise HTTPException(status_code=422, detail="Set parent introuvable.")


@router.get("", response_model=list[FilterSetOut])
def list_filter_sets(
    parent_id: int | None = None, db: Session = Depends(get_db)
) -> list[FilterSet]:
    stmt = select(FilterSet).order_by(FilterSet.updated_at.desc())
    if parent_id is not None:
        stmt = stmt.where(FilterSet.parent_id == parent_id)
    return list(db.execute(stmt).scalars())


@router.post("", response_model=FilterSetOut, status_code=201)
def create_filter_set(payload: FilterSetIn, db: Session = Depends(get_db)) -> FilterSet:
    _check_parent(db, payload.parent_id)
    fs = FilterSet(
        name=payload.name,
        description=payload.description,
        criteria=payload.criteria.model_dump(exclude_none=True),
        parent_id=payload.parent_id,
    )
    db.add(fs)
    db.commit()
    db.refresh(fs)
    return fs


@router.get("/{fs_id}", response_model=FilterSetOut)
def get_filter_set(fs_id: int, db: Session = Depends(get_db)) -> FilterSet:
    return _get_or_404(db, fs_id)


@router.get("/{fs_id}/resolved", response_model=FilterSetResolvedOut)
def get_filter_set_resolved(fs_id: int, db: Session = Depends(get_db)) -> dict:
    """Set avec critères effectifs après héritage parent → enfant."""
    fs = _get_or_404(db, fs_id)
    out = FilterSetOut.model_validate(fs).model_dump()
    out["resolved_criteria"] = resolve_criteria(fs)
    return out


@router.put("/{fs_id}", response_model=FilterSetOut)
def update_filter_set(fs_id: int, payload: FilterSetIn, db: Session = Depends(get_db)) -> FilterSet:
    fs = _get_or_404(db, fs_id)
    _check_parent(db, payload.parent_id, self_id=fs_id)
    fs.name = payload.name
    fs.description = payload.description
    fs.criteria = payload.criteria.model_dump(exclude_none=True)
    fs.parent_id = payload.parent_id
    db.commit()
    db.refresh(fs)
    return fs


@router.delete("/{fs_id}", status_code=204, response_class=Response)
def delete_filter_set(fs_id: int, db: Session = Depends(get_db)) -> Response:
    fs = _get_or_404(db, fs_id)
    db.delete(fs)
    db.commit()
    return Response(status_code=204)
