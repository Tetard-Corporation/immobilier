"""Exécution de recherches : appel source, normalisation, persistance."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Listing, PriceHistory
from ..schemas import ListingOut, SearchCriteria, SearchResultOut
from ..sources import NormalizedListing, resolve_source
from .dedup import dedupe, fingerprint
from .preferences import evaluate as evaluate_preferences


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
        nb_chambres=item.nb_chambres,
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
        condition=flags.get("condition"),
        niveau_travaux=flags.get("niveau_travaux"),
        features=flags.get("features") or [],
        nuisances=flags.get("nuisances") or [],
        nature_score=flags.get("nature_score") or 0,
        nature_exception=bool(flags.get("nature_exception")),
        score=flags.get("score"),
        score_details=flags.get("score_details") or [],
        constructible=flags.get("constructible"),
        est_zone_au=flags.get("est_zone_au"),
        zone_urba=flags.get("zone_urba"),
        altitude=flags.get("altitude"),
        rail_time_min=flags.get("rail_time_min"),
        risques=flags.get("risques") or [],
        prix_m2_secteur=flags.get("prix_m2_secteur"),
        ecart_prix_pct=flags.get("ecart_prix_pct"),
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
        nb_chambres=item.nb_chambres,
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
        condition=(item.flags or {}).get("condition"),
        niveau_travaux=(item.flags or {}).get("niveau_travaux"),
        features=(item.flags or {}).get("features") or [],
        nuisances=(item.flags or {}).get("nuisances") or [],
        nature_score=(item.flags or {}).get("nature_score") or 0,
        nature_exception=bool((item.flags or {}).get("nature_exception")),
        score=(item.flags or {}).get("score"),
        score_details=(item.flags or {}).get("score_details") or [],
        constructible=(item.flags or {}).get("constructible"),
        est_zone_au=(item.flags or {}).get("est_zone_au"),
        zone_urba=(item.flags or {}).get("zone_urba"),
        altitude=(item.flags or {}).get("altitude"),
        rail_time_min=(item.flags or {}).get("rail_time_min"),
        risques=(item.flags or {}).get("risques") or [],
        prix_m2_secteur=(item.flags or {}).get("prix_m2_secteur"),
        ecart_prix_pct=(item.flags or {}).get("ecart_prix_pct"),
        price_decreased=bool((item.flags or {}).get("price_decreased")),
        canonical_id=fingerprint(item),
        prix_m2_terrain=item.prix_m2_terrain,
        is_new=is_new,
    )


def run_search(
    db: Session,
    source_name: str | None,
    criteria: SearchCriteria,
    *,
    dedupe_results: bool = False,
    sort_by_score: bool = False,
    enrich: bool = False,
) -> SearchResultOut:
    """Recherche ad hoc : exécute, persiste les listings et renvoie le résultat normalisé."""
    source = resolve_source(source_name)
    result = source.search(criteria)
    items = dedupe(result.items) if dedupe_results else result.items
    if enrich or get_settings().enrich_on_search:
        from ..enrichment import enrich_listing

        items = [enrich_listing(it) for it in items]
    if sort_by_score:
        items = sorted(items, key=lambda it: (it.flags or {}).get("score") or 0, reverse=True)
    out_items: list[ListingOut] = []
    for item in items:
        row = upsert_listing(db, item)
        out = to_listing_out(item, db_id=row.id)
        if criteria.preferences:
            match, details = evaluate_preferences(item, criteria.preferences)
            out.match_score = match
            out.match_details = details
        out_items.append(out)
    db.commit()

    # Régime ranking : classement par match_score décroissant.
    if criteria.preferences:
        out_items.sort(key=lambda o: o.match_score if o.match_score is not None else -1, reverse=True)
    return SearchResultOut(
        source=source.name,
        total=result.total,
        page=criteria.page,
        par_page=criteria.par_page,
        curseur_suivant=result.curseur_suivant,
        credits_estimes=result.credits_estimes,
        results=out_items,
    )
