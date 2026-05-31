"""Abstraction des sources de données (Pappers, mock, et futures sources)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..schemas import SearchCriteria


@dataclass
class NormalizedListing:
    """Représentation d'un bien/parcelle commune à toutes les sources."""

    source: str
    external_id: str
    type_bien: str | None = None
    prix: float | None = None
    surface_terrain: float | None = None
    surface_bati: float | None = None
    nb_pieces: int | None = None
    adresse: str | None = None
    commune: str | None = None
    code_postal: str | None = None
    code_commune: str | None = None
    departement: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    parcelle: str | None = None
    date_mutation: str | None = None
    dpe_classe: str | None = None
    url: str | None = None
    description: str | None = None
    # Drapeaux calculés : {"ruine": bool, "a_renover": bool, "price_decreased": bool}
    flags: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def prix_m2_terrain(self) -> float | None:
        if self.prix and self.surface_terrain:
            return round(self.prix / self.surface_terrain, 2)
        return None


@dataclass
class SearchResult:
    items: list[NormalizedListing]
    total: int | None = None
    curseur_suivant: str | None = None
    credits_estimes: int = 0


class ListingSource(ABC):
    """Interface commune. Une nouvelle source (PAP, Leboncoin...) implémente ces méthodes."""

    name: str = "base"
    label: str = "Base"

    @property
    @abstractmethod
    def available(self) -> bool:
        """Indique si la source est utilisable (ex. clé API présente)."""

    @abstractmethod
    def search(self, criteria: SearchCriteria) -> SearchResult:
        """Exécute une recherche et renvoie des annonces normalisées."""

    @abstractmethod
    def get(self, external_id: str, bases: list[str] | None = None) -> NormalizedListing | None:
        """Renvoie le détail enrichi d'une annonce."""
