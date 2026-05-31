"""Modèles ORM SQLAlchemy."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FilterSet(Base):
    """Un jeu de filtres réutilisable."""

    __tablename__ = "filter_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Critères de recherche normalisés (voir schemas.SearchCriteria).
    criteria: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    saved_searches: Mapped[list["SavedSearch"]] = relationship(
        back_populates="filter_set", cascade="all, delete-orphan"
    )


class SavedSearch(Base):
    """Une recherche fréquente : critères + fréquence + suivi des nouveautés."""

    __tablename__ = "saved_searches"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="auto")
    # Critères inline (copie autonome) ; éventuellement issus d'un FilterSet.
    criteria: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    filter_set_id: Mapped[int | None] = mapped_column(
        ForeignKey("filter_sets.id", ondelete="SET NULL"), nullable=True
    )
    # 0 = manuel ; sinon intervalle en minutes (60 = horaire, 1440 = quotidien).
    frequency_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    filter_set: Mapped[FilterSet | None] = relationship(back_populates="saved_searches")
    runs: Mapped[list["SearchRun"]] = relationship(
        back_populates="saved_search", cascade="all, delete-orphan"
    )
    seen: Mapped[list["SeenListing"]] = relationship(
        back_populates="saved_search", cascade="all, delete-orphan"
    )


class Listing(Base):
    """Un bien/parcelle normalisé, persisté pour l'affichage et le diff de nouveautés."""

    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_source_external"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)

    type_bien: Mapped[str | None] = mapped_column(String(60), nullable=True)
    prix: Mapped[float | None] = mapped_column(Float, nullable=True)
    surface_terrain: Mapped[float | None] = mapped_column(Float, nullable=True)
    surface_bati: Mapped[float | None] = mapped_column(Float, nullable=True)
    nb_pieces: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nb_chambres: Mapped[int | None] = mapped_column(Integer, nullable=True)

    adresse: Mapped[str | None] = mapped_column(String(300), nullable=True)
    commune: Mapped[str | None] = mapped_column(String(150), nullable=True)
    code_postal: Mapped[str | None] = mapped_column(String(10), nullable=True)
    code_commune: Mapped[str | None] = mapped_column(String(10), nullable=True)
    departement: Mapped[str | None] = mapped_column(String(60), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    parcelle: Mapped[str | None] = mapped_column(String(40), nullable=True)

    date_mutation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dpe_classe: Mapped[str | None] = mapped_column(String(4), nullable=True)
    url: Mapped[str | None] = mapped_column(String(400), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Classification : état du bâti
    condition: Mapped[str | None] = mapped_column(String(20), nullable=True)
    niveau_travaux: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Classification : qualité/nature du terrain
    features: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    nuisances: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    nature_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nature_exception: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    price_decreased: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Score d'investissement (0–100) + détail explicable des contributions.
    score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    score_details: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Enrichissement (Lot A) : données officielles géolocalisées.
    constructible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    est_zone_au: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    zone_urba: Mapped[str | None] = mapped_column(String(10), nullable=True)
    altitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    rail_time_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risques: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Empreinte de dédoublonnage inter-sources (biens identiques regroupés).
    canonical_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    raw: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class PriceHistory(Base):
    """Historique de prix d'un bien, pour détecter les baisses/re-publications."""

    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prix: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SearchRun(Base):
    """Historique d'exécution d'une recherche fréquente."""

    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    saved_search_id: Mapped[int] = mapped_column(
        ForeignKey("saved_searches.id", ondelete="CASCADE"), nullable=False
    )
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    nb_results: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nb_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    credits_estimes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    saved_search: Mapped[SavedSearch] = relationship(back_populates="runs")


class SeenListing(Base):
    """Trace les annonces déjà vues par une recherche, pour détecter les nouveautés."""

    __tablename__ = "seen_listings"
    __table_args__ = (
        UniqueConstraint("saved_search_id", "external_id", name="uq_search_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    saved_search_id: Mapped[int] = mapped_column(
        ForeignKey("saved_searches.id", ondelete="CASCADE"), nullable=False
    )
    listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    is_new: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    saved_search: Mapped[SavedSearch] = relationship(back_populates="seen")
    listing: Mapped[Listing | None] = relationship()
