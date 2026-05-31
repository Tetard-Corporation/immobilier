"""Exécution de recherches : appel source, normalisation, persistance."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Listing, PriceHistory
from ..schemas import ListingOut, SearchCriteria, SearchResultOut
from ..sources import NormalizedListing, resolve_source
from .dedup import dedupe, fingerprint


def upsert_listing(db: Session, item: NormalizedListing) -> Listing:
    """Insère ou met à jour un listing par (source, external_id).

    Gère aussi l'historique de prix (détection de baisse) et l'empreinte de
    dédoublonnage inter-sources.
    """
    stmt = select(Listing).where(
        Listing.source == item.source, Listing.external_id == item.external_id
    )
    row = db.execute(stmt).scalar_one_or_none()
    flags = item.flags or {}
    price_decreased = bool(flags.get("price_decreased"))

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
        description=item.description,
        flag_ruine=bool(flags.get("ruine")),
        flag_a_renover=bool(flags.get("a_renover")),
        canonical_id=fingerprint(item),
        raw=item.raw,
    )
    if row is None:
        row = Listing(
            source=item.source,
            external_id=item.external_id,
            price_decreased=price_decreased,
            **fields,
        )
        db.add(row)
        db.flush()
        if item.prix is not None:
            db.add(PriceHistory(listing_id=row.id, prix=item.prix))
    else:
        old_price = row.prix
        for key, value in fields.items():
            setattr(row, key, value)
        # Historise et marque une éventuelle baisse de prix.
        if item.prix is not None and old_price is not None and item.prix != old_price:
            db.add(PriceHistory(listing_id=row.id, prix=item.prix))
            row.price_decreased = item.prix < old_price
        elif price_decreased:
            row.price_decreased = True
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
        description=item.description,
        flag_ruine=bool((item.flags or {}).get("ruine")),
        flag_a_renover=bool((item.flags or {}).get("a_renover")),
        price_decreased=bool((item.flags or {}).get("price_decreased")),
        canonical_id=fingerprint(item),
        prix_m2_terrain=item.prix_m2_terrain,
        is_new=is_new,
    )


def run_search(
    db: Session, source_name: str | None, criteria: SearchCriteria, *, dedupe_results: bool = False
) -> SearchResultOut:
    """Recherche ad hoc : exécute, persiste les listings et renvoie le résultat normalisé."""
    source = resolve_source(source_name)
    result = source.search(criteria)
    items = dedupe(result.items) if dedupe_results else result.items
    out_items: list[ListingOut] = []
    for item in items:
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
