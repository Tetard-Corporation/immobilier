"""Schémas Pydantic (entrées/sorties de l'API)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Critères de recherche (communs à toutes les sources)
# --------------------------------------------------------------------------- #
class Preference(BaseModel):
    """Préférence pondérée (régime ranking) : ne filtre pas, augmente le match_score.

    `kind` ∈ preferences.PREFERENCE_KINDS ; `params` dépend du kind.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str
    weight: float = 1.0
    params: dict = Field(default_factory=dict)
    label: str | None = None


class SearchCriteria(BaseModel):
    """Critères normalisés, indépendants de la source de données."""

    model_config = ConfigDict(extra="forbid")

    # Préférences pondérées (classement) — voir services/preferences.py.
    preferences: list[Preference] | None = None

    # Localisation
    code_postal: str | None = None
    code_commune: str | None = None
    departement: str | None = None
    region: str | None = None
    adresse: str | None = None
    secteur: str | None = Field(
        default=None, description="Nom de secteur/commune pivot (ex. 'École 73', 'Bauges')."
    )
    latitude: float | None = None
    longitude: float | None = None
    distance: int | None = Field(default=None, description="Rayon en mètres autour du point.")

    # Prix
    prix_min: float | None = None
    prix_max: float | None = None

    # Surfaces
    surface_terrain_min: float | None = None
    surface_terrain_max: float | None = None
    surface_bati_min: float | None = None
    surface_bati_max: float | None = None

    # Pièces / chambres
    nb_pieces_min: int | None = None
    nb_pieces_max: int | None = None
    nb_chambres_min: int | None = None

    # Type de bien (vocabulaire source-agnostique : voir filters.PROPERTY_TYPES)
    property_types: list[str] | None = Field(
        default=None,
        description="terrain, maison, appartement, immeuble, local_commercial, parking",
    )

    # Type de local Pappers (contrôle fin, optionnel)
    types_local: list[str] | None = Field(
        default=None,
        description="appartement, maison, dependance, local_industriel_commercial_ou_assimile",
    )
    natures_vente: list[str] | None = Field(
        default=None,
        description="vente, vente_terrain_batir, vente_futur_achevement, echange, adjudication...",
    )

    # Dates de mutation
    date_vente_min: str | None = Field(default=None, description="AAAA-MM-JJ")
    date_vente_max: str | None = Field(default=None, description="AAAA-MM-JJ")

    # Bâtiment
    annee_construction_min: int | None = None
    annee_construction_max: int | None = None

    # DPE
    dpe_classes: list[str] | None = Field(default=None, description="A, B, C, D, E, F, G")

    # État / niveau de travaux (échelle : habitable, rafraichir, renover, gros_travaux, ruine)
    conditions: list[str] | None = Field(
        default=None,
        description="Niveaux d'état à conserver : habitable, rafraichir, renover, gros_travaux, ruine",
    )
    niveau_travaux_max: int | None = Field(
        default=None, description="Niveau de travaux maximal accepté (0=habitable ... 4=ruine)."
    )

    # Qualité / nature du terrain
    features: list[str] | None = Field(
        default=None,
        description="Aménités requises (toutes) : vue, foret, eau, calme, isole, "
        "sans_vis_a_vis, arbore, ensoleille",
    )
    nature_exception: bool | None = Field(
        default=None, description="Ne garder que les biens 'nature d'exception'."
    )
    nature_score_min: int | None = Field(default=None, description="Score nature minimal.")

    # Enrichissement (Lot A) — actifs si la recherche est lancée avec enrich
    constructible_only: bool | None = Field(default=None, description="Ne garder que les biens constructibles.")
    zones_urba: list[str] | None = Field(default=None, description="Zones d'urbanisme (U, AU, A, N).")
    exclure_risques: list[str] | None = Field(default=None, description="Risques à exclure (inondation, ...).")
    eau_conforme_only: bool | None = Field(default=None, description="Ne garder que les communes à eau potable conforme.")
    altitude_min: float | None = None
    altitude_max: float | None = None

    # Aide à la décision
    score_min: float | None = Field(default=None, description="Score d'investissement minimal (0-100).")

    price_decreased: bool | None = Field(
        default=None, description="Ne garder que les annonces en baisse de prix."
    )

    # Bases Pappers à inclure (override du défaut). Impacte la consommation de crédits.
    bases: list[str] | None = None

    # Pagination
    page: int = 1
    par_page: int = Field(default=20, ge=1, le=100)
    curseur: str | None = None


# --------------------------------------------------------------------------- #
# Résultats de recherche
# --------------------------------------------------------------------------- #
class ListingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    source: str
    external_id: str
    type_bien: str | None = None
    prix: float | None = None
    surface_terrain: float | None = None
    surface_bati: float | None = None
    nb_pieces: int | None = None
    nb_chambres: int | None = None
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
    condition: str | None = None
    niveau_travaux: int | None = None
    features: list[str] = []
    nuisances: list[str] = []
    nature_score: int = 0
    nature_exception: bool = False
    price_decreased: bool = False
    score: float | None = None
    score_details: list = []
    match_score: float | None = None
    match_details: list = []
    # Enrichissement (Lot A)
    constructible: bool | None = None
    est_zone_au: bool | None = None
    zone_urba: str | None = None
    altitude: float | None = None
    rail_time_min: int | None = None
    risques: list = []
    prix_m2_secteur: float | None = None
    ecart_prix_pct: float | None = None
    pollution_eau_score: float | None = None
    eau_potable_conforme: bool | None = None
    pollutions: list = []
    age_median: float | None = None
    part_gauche: float | None = None
    canonical_id: str | None = None
    prix_m2_terrain: float | None = None
    is_new: bool | None = None


class SearchResultOut(BaseModel):
    source: str
    total: int | None = None
    page: int
    par_page: int
    curseur_suivant: str | None = None
    credits_estimes: int = 0
    results: list[ListingOut]


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
class SourceInfo(BaseModel):
    name: str
    label: str
    available: bool
    is_default: bool
    note: str | None = None


# --------------------------------------------------------------------------- #
# Filter sets
# --------------------------------------------------------------------------- #
class FilterSetIn(BaseModel):
    name: str
    description: str | None = None
    criteria: SearchCriteria
    parent_id: int | None = Field(default=None, description="Set parent (pour un sous-set).")


class FilterSetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    criteria: dict
    parent_id: int | None = None
    created_at: datetime
    updated_at: datetime


class FilterSetResolvedOut(FilterSetOut):
    """Set + critères effectifs après héritage parent → enfant."""

    resolved_criteria: dict


class SavedListingIn(BaseModel):
    listing_id: int | None = None
    source: str | None = None
    external_id: str | None = None
    filter_set_id: int | None = None
    note: str | None = None
    # Snapshot facultatif (sinon reconstruit depuis le listing en base).
    snapshot: dict | None = None


class SavedListingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_id: int | None = None
    source: str
    external_id: str
    filter_set_id: int | None = None
    note: str | None = None
    snapshot: dict
    saved_at: datetime


# --------------------------------------------------------------------------- #
# Saved searches
# --------------------------------------------------------------------------- #
class SavedSearchIn(BaseModel):
    name: str
    source: str = "auto"
    criteria: SearchCriteria | None = None
    filter_set_id: int | None = None
    frequency_minutes: int = 0
    enabled: bool = True


class SavedSearchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source: str
    criteria: dict
    filter_set_id: int | None = None
    frequency_minutes: int
    enabled: bool
    last_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    nb_new: int = 0


class SearchRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ran_at: datetime
    nb_results: int
    nb_new: int
    credits_estimes: int
    error: str | None = None
