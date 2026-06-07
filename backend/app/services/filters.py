"""Schéma des filtres (pour le futur front) et filtrage en mémoire (utilisé par le mock)."""

from __future__ import annotations

from ..schemas import SearchCriteria
from ..sources.base import NormalizedListing
from .classify import CONDITIONS
from .quality import FEATURES

# Vocabulaire de type de bien, source-agnostique (chaque source le mappe).
PROPERTY_TYPES = ["terrain", "maison", "appartement", "immeuble", "local_commercial", "parking"]

# Valeurs d'énumération issues de la spec Pappers Immobilier.
TYPES_LOCAL = ["appartement", "maison", "dependance", "local_industriel_commercial_ou_assimile"]
NATURES_VENTE = [
    "vente",
    "vente_terrain_batir",
    "vente_futur_achevement",
    "echange",
    "adjudication",
    "expropriation",
]
DPE_CLASSES = ["A", "B", "C", "D", "E", "F", "G"]


def get_filter_schema() -> dict:
    """Description structurée des filtres disponibles (groupes, types, énumérations).

    Sert de contrat pour construire dynamiquement le formulaire côté front.
    """

    return {
        "groups": [
            {
                "key": "localisation",
                "label": "Localisation",
                "fields": [
                    {"name": "code_postal", "type": "string", "label": "Code postal"},
                    {"name": "code_commune", "type": "string", "label": "Code commune (INSEE)"},
                    {"name": "departement", "type": "string", "label": "Département"},
                    {"name": "region", "type": "string", "label": "Région"},
                    {"name": "adresse", "type": "string", "label": "Adresse"},
                    {"name": "latitude", "type": "number", "label": "Latitude"},
                    {"name": "longitude", "type": "number", "label": "Longitude"},
                    {"name": "distance", "type": "integer", "label": "Rayon (m)"},
                ],
            },
            {
                "key": "prix",
                "label": "Prix",
                "fields": [
                    {"name": "prix_min", "type": "number", "label": "Prix min (€)"},
                    {"name": "prix_max", "type": "number", "label": "Prix max (€)"},
                ],
            },
            {
                "key": "surfaces",
                "label": "Surfaces",
                "fields": [
                    {"name": "surface_terrain_min", "type": "number", "label": "Terrain min (m²)"},
                    {"name": "surface_terrain_max", "type": "number", "label": "Terrain max (m²)"},
                    {"name": "surface_bati_min", "type": "number", "label": "Bâti min (m²)"},
                    {"name": "surface_bati_max", "type": "number", "label": "Bâti max (m²)"},
                ],
            },
            {
                "key": "bien",
                "label": "Type de bien",
                "fields": [
                    {
                        "name": "property_types",
                        "type": "multiselect",
                        "label": "Type de bien",
                        "options": PROPERTY_TYPES,
                    },
                    {
                        "name": "types_local",
                        "type": "multiselect",
                        "label": "Type de local (Pappers)",
                        "options": TYPES_LOCAL,
                    },
                    {
                        "name": "natures_vente",
                        "type": "multiselect",
                        "label": "Nature de mutation",
                        "options": NATURES_VENTE,
                    },
                    {"name": "nb_pieces_min", "type": "integer", "label": "Pièces min"},
                    {"name": "nb_pieces_max", "type": "integer", "label": "Pièces max"},
                ],
            },
            {
                "key": "batiment",
                "label": "Bâtiment",
                "fields": [
                    {"name": "annee_construction_min", "type": "integer", "label": "Année min"},
                    {"name": "annee_construction_max", "type": "integer", "label": "Année max"},
                ],
            },
            {
                "key": "dpe",
                "label": "DPE",
                "fields": [
                    {
                        "name": "dpe_classes",
                        "type": "multiselect",
                        "label": "Classe énergie",
                        "options": DPE_CLASSES,
                    },
                ],
            },
            {
                "key": "etat",
                "label": "État / opportunité",
                "fields": [
                    {
                        "name": "conditions",
                        "type": "multiselect",
                        "label": "État / niveau de travaux",
                        "options": CONDITIONS,
                    },
                    {"name": "niveau_travaux_max", "type": "integer", "label": "Travaux max (0–4)"},
                    {"name": "price_decreased", "type": "boolean", "label": "Baisse de prix"},
                ],
            },
            {
                "key": "nature",
                "label": "Qualité / nature",
                "fields": [
                    {
                        "name": "features",
                        "type": "multiselect",
                        "label": "Aménités",
                        "options": FEATURES,
                    },
                    {"name": "nature_exception", "type": "boolean", "label": "Nature d'exception"},
                    {"name": "nature_score_min", "type": "integer", "label": "Score nature min"},
                ],
            },
            {
                "key": "urbanisme",
                "label": "Urbanisme / environnement (enrichissement)",
                "fields": [
                    {"name": "constructible_only", "type": "boolean", "label": "Constructible uniquement"},
                    {"name": "zones_urba", "type": "multiselect", "label": "Zone PLU", "options": ["U", "AU", "A", "N"]},
                    {"name": "exclure_risques", "type": "multiselect", "label": "Exclure risques",
                     "options": ["inondation", "retraitGonflementArgile", "seisme", "radon", "remonteeNappe", "mouvementTerrain"]},
                    {"name": "eau_conforme_only", "type": "boolean", "label": "Eau potable conforme"},
                    {"name": "altitude_min", "type": "number", "label": "Altitude min (m)"},
                    {"name": "altitude_max", "type": "number", "label": "Altitude max (m)"},
                ],
            },
            {
                "key": "decision",
                "label": "Aide à la décision",
                "fields": [
                    {"name": "score_min", "type": "number", "label": "Score d'investissement min (0-100)"},
                ],
            },
            {
                "key": "dates",
                "label": "Dates de mutation",
                "fields": [
                    {"name": "date_vente_min", "type": "date", "label": "Depuis"},
                    {"name": "date_vente_max", "type": "date", "label": "Jusqu'à"},
                ],
            },
        ]
    }


def _in_range(value, vmin, vmax) -> bool:
    if value is None:
        # Si un seuil est imposé mais la valeur est inconnue, on exclut.
        return vmin is None and vmax is None
    if vmin is not None and value < vmin:
        return False
    if vmax is not None and value > vmax:
        return False
    return True


def matches(listing: NormalizedListing, c: SearchCriteria) -> bool:
    """Filtrage en mémoire d'une annonce selon les critères. Utilisé par la source mock."""

    if c.code_postal and listing.code_postal != c.code_postal:
        return False
    if c.code_commune and listing.code_commune != c.code_commune:
        return False
    if c.departement and (listing.departement or "").lower() != c.departement.lower():
        return False
    if c.adresse and c.adresse.lower() not in (listing.adresse or "").lower():
        return False

    # Filtre géographique par point + rayon (lat/lon/distance en mètres).
    if c.latitude is not None and c.longitude is not None and c.distance:
        if listing.latitude is None or listing.longitude is None:
            return False
        from .geo import haversine_km

        if haversine_km(c.latitude, c.longitude, listing.latitude, listing.longitude) > c.distance / 1000.0:
            return False

    if not _in_range(listing.prix, c.prix_min, c.prix_max):
        return False
    if not _in_range(listing.surface_terrain, c.surface_terrain_min, c.surface_terrain_max):
        return False
    if not _in_range(listing.surface_bati, c.surface_bati_min, c.surface_bati_max):
        return False
    if not _in_range(listing.nb_pieces, c.nb_pieces_min, c.nb_pieces_max):
        return False
    if c.nb_chambres_min is not None and (listing.nb_chambres or 0) < c.nb_chambres_min:
        return False

    if c.property_types and (listing.type_bien not in c.property_types):
        return False
    if c.types_local and (listing.type_bien not in c.types_local):
        return False
    if c.dpe_classes and (listing.dpe_classe not in c.dpe_classes):
        return False

    condition = listing.flags.get("condition")
    niveau = listing.flags.get("niveau_travaux")
    if c.conditions and condition not in c.conditions:
        return False
    if c.niveau_travaux_max is not None and (niveau is None or niveau > c.niveau_travaux_max):
        return False

    feats = set(listing.flags.get("features") or [])
    if c.features and not set(c.features) <= feats:
        return False
    if c.nature_exception and not listing.flags.get("nature_exception"):
        return False
    if c.nature_score_min is not None and (listing.flags.get("nature_score") or 0) < c.nature_score_min:
        return False

    if c.price_decreased and not listing.flags.get("price_decreased"):
        return False
    if c.score_min is not None and (listing.flags.get("score") or 0) < c.score_min:
        return False

    # Enrichissement (n'exclut que si la donnée est présente).
    if c.constructible_only and listing.flags.get("constructible") is False:
        return False
    if c.zones_urba and listing.flags.get("zone_urba") and listing.flags["zone_urba"] not in c.zones_urba:
        return False
    if c.exclure_risques and set(c.exclure_risques) & set(listing.flags.get("risques") or []):
        return False
    if c.eau_conforme_only and listing.flags.get("eau_potable_conforme") is False:
        return False
    alt = listing.flags.get("altitude")
    if c.altitude_min is not None and alt is not None and alt < c.altitude_min:
        return False
    if c.altitude_max is not None and alt is not None and alt > c.altitude_max:
        return False

    if c.date_vente_min and (listing.date_mutation or "") < c.date_vente_min:
        return False
    if c.date_vente_max and (listing.date_mutation or "9999") > c.date_vente_max:
        return False

    return True
