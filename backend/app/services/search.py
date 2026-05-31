"""Exécution de recherches : appel source, normalisation, persistance."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Listing
from ..schemas import ListingOut, SearchCriteria, SearchResultOut
from ..sources import NormalizedListing, resolve_source


def upsert_listing(db: Session, item: NormalizedListing) -> Listing:
    """Insère ou met à jour un listing par (source, external_id)."""
    stmt = select(Listing).where(
        Listing.source == item.source, Listing.external_id == item.external_id
    )
    row = db.execute(stmt).scalar_one_or_none()
    fields = dict(
        type_bien=item.type_bien,
        prix=item.prix,
        surface_terrain=item.surface_terrain,
        surface_bati=item.surface_bati,
        nb_pieces=item.nb_pieces,
        adresse=item.adresse,
        commune=item.commune,
        code_postal=item.code_postal,
        code_commune=item.code_commune,
        departement=item.departement,
        latitude=item.latitude,
        longitude=item.longitude,
        parcelle=item.parcelle,
        date_mutation=item.date_mutation,
        dpe_classe=item.dpe_classe,
        url=item.url,
        raw=item.raw,
    )
    if row is None:
        row = Listing(source=item.source, external_id=item.external_id, **fields)
        db.add(row)
    else:
        for key, value in fields.items():
            setattr(row, key, value)
    db.flush()
    return row


def to_listing_out(item: NormalizedListing, *, db_id: int | None = None, is_new: bool | None = None) -> ListingOut:
    return ListingOut(
        id=db_id,
        source=item.source,
        external_id=item.external_id,
        type_bien=item.type_bien,
        prix=item.prix,
        surface_terrain=item.surface_terrain,
        surface_bati=item.surface_bati,
        nb_pieces=item.nb_pieces,
        adresse=item.adresse,
        commune=item.commune,
        code_postal=item.code_postal,
        code_commune=item.code_commune,
        departement=item.departement,
        latitude=item.latitude,
        longitude=item.longitude,
        parcelle=item.parcelle,
        date_mutation=item.date_mutation,
        dpe_classe=item.dpe_classe,
        url=item.url,
        prix_m2_terrain=item.prix_m2_terrain,
        is_new=is_new,
    )


def run_search(db: Session, source_name: str | None, criteria: SearchCriteria) -> SearchResultOut:
    """Recherche ad hoc : exécute, persiste les listings et renvoie le résultat normalisé."""
    source = resolve_source(source_name)
    result = source.search(criteria)
    out_items: list[ListingOut] = []
    for item in result.items:
        row = upsert_listing(db, item)
        out_items.append(to_listing_out(item, db_id=row.id))
    db.commit()
    return SearchResultOut(
        source=source.name,
        total=result.total,
        page=criteria.page,
        par_page=criteria.par_page,
        curseur_suivant=result.curseur_suivant,
        credits_estimes=result.credits_estimes,
        results=out_items,
    )
