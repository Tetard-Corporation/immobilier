"""Source "agences" : interroge les annonces déjà ingérées en base.

Contrairement aux autres sources (pull en temps réel), c'est une source "inbound" :
les annonces sont alimentées en amont par `services.agences_ingest` (emails + sites)
puis simplement filtrées ici.
"""

from __future__ import annotations

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Listing
from ..schemas import SearchCriteria
from ..services.filters import matches
from .base import ListingSource, NormalizedListing, SearchResult


def normalized_from_listing(row: Listing) -> NormalizedListing:
    """Reconstruit une annonce normalisée (avec flags) depuis une ligne persistée."""
    return NormalizedListing(
        source=row.source,
        external_id=row.external_id,
        type_bien=row.type_bien,
        prix=row.prix,
        surface_terrain=row.surface_terrain,
        surface_bati=row.surface_bati,
        nb_pieces=row.nb_pieces,
        nb_chambres=row.nb_chambres,
        adresse=row.adresse,
        commune=row.commune,
        code_postal=row.code_postal,
        code_commune=row.code_commune,
        departement=row.departement,
        latitude=row.latitude,
        longitude=row.longitude,
        parcelle=row.parcelle,
        date_mutation=row.date_mutation,
        dpe_classe=row.dpe_classe,
        url=row.url,
        description=row.description,
        flags={
            "condition": row.condition,
            "niveau_travaux": row.niveau_travaux,
            "features": row.features or [],
            "nuisances": row.nuisances or [],
            "nature_score": row.nature_score,
            "nature_exception": row.nature_exception,
            "price_decreased": row.price_decreased,
            "score": row.score,
            "score_details": row.score_details or [],
            "constructible": row.constructible,
            "est_zone_au": row.est_zone_au,
            "zone_urba": row.zone_urba,
            "altitude": row.altitude,
            "rail_time_min": row.rail_time_min,
            "risques": row.risques or [],
            "prix_m2_secteur": row.prix_m2_secteur,
            "ecart_prix_pct": row.ecart_prix_pct,
            "pollution_eau_score": row.pollution_eau_score,
            "eau_potable_conforme": row.eau_potable_conforme,
            "pollutions": row.pollutions or [],
            "age_median": row.age_median,
            "part_gauche": row.part_gauche,
            "pop_jeune_score": round(max(0.0, min(1.0, 1 - (row.age_median - 30) / 25)), 3) if row.age_median is not None else None,
            "orientation_gauche_score": row.part_gauche,
            "population_commune": row.population_commune,
            "isolement_score": row.isolement_score,
        },
        raw=row.raw or {},
    )


class AgencesSource(ListingSource):
    name = "agences"
    label = "Agences (newsletters + sites)"

    @property
    def available(self) -> bool:
        return True

    def search(self, criteria: SearchCriteria) -> SearchResult:
        db = SessionLocal()
        try:
            rows = db.execute(
                select(Listing)
                .where(Listing.source == "agences")
                .order_by(Listing.first_seen_at.desc())
            ).scalars()
            items = [normalized_from_listing(r) for r in rows]
        finally:
            db.close()
        filtered = [it for it in items if matches(it, criteria)]
        total = len(filtered)
        start = (criteria.page - 1) * criteria.par_page
        page = filtered[start : start + criteria.par_page]
        return SearchResult(items=page, total=total, curseur_suivant=None, credits_estimes=0)

    def get(self, external_id: str, bases: list[str] | None = None) -> NormalizedListing | None:
        db = SessionLocal()
        try:
            row = db.execute(
                select(Listing).where(
                    Listing.source == "agences", Listing.external_id == external_id
                )
            ).scalar_one_or_none()
            return normalized_from_listing(row) if row else None
        finally:
            db.close()
