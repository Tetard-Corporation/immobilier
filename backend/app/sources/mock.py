"""Source factice : jeu de données réaliste pour développer/tester sans clé Pappers.

Reproduit la forme des données Pappers Immobilier (parcelle + vente la plus récente)
afin que la couche de normalisation et les filtres se comportent comme en réel.
"""

from __future__ import annotations

from ..schemas import SearchCriteria
from ..services.enrich import annotate
from ..services.filters import matches
from .base import ListingSource, NormalizedListing, SearchResult

# (commune, code_commune, code_postal, departement, lat, lng)
_COMMUNES = [
    ("TOULOUSE", "31555", "31000", "HAUTE-GARONNE", 43.6045, 1.4440),
    ("BORDEAUX", "33063", "33000", "GIRONDE", 44.8378, -0.5792),
    ("NANTES", "44109", "44000", "LOIRE-ATLANTIQUE", 47.2184, -1.5536),
    ("MONTPELLIER", "34172", "34000", "HERAULT", 43.6108, 3.8767),
]

_TYPES = [
    ("terrain", None, None),  # type_bien, surface_bati, nb_pieces (terrain nu)
    ("maison", 95.0, 4),
    ("appartement", 62.0, 3),
    ("maison", 140.0, 6),
]


_DESCRIPTIONS = [
    "Terrain plat et viabilisé, proche commerces et écoles.",
    "Magnifique terrain avec vue dégagée, en pleine nature, sans vis-à-vis, joliment arboré.",
    "Maison à rénover, prévoir des travaux de rafraîchissement.",
    "Ancienne bâtisse en ruine à reconstruire, au calme en lisière de forêt.",
]


def _build_dataset() -> list[NormalizedListing]:
    listings: list[NormalizedListing] = []
    idx = 0
    for ci, (commune, cc, cp, dep, lat, lng) in enumerate(_COMMUNES):
        for ti, (type_bien, sbati, pieces) in enumerate(_TYPES):
            idx += 1
            surface_terrain = 200.0 + (idx * 37) % 800
            prix = 60_000 + ((idx * 17_500) % 540_000)
            dpe = None if type_bien == "terrain" else "ABCDEFG"[(idx * 3) % 7]
            parcelle = f"{cc}000A{1000 + idx:04d}"
            date = f"2025-{(idx % 12) + 1:02d}-{(idx % 27) + 1:02d}"
            nature = "vente_terrain_batir" if type_bien == "terrain" else "vente"
            description = _DESCRIPTIONS[idx % len(_DESCRIPTIONS)]
            listings.append(
                NormalizedListing(
                    source="mock",
                    external_id=parcelle,
                    type_bien=type_bien,
                    prix=float(prix),
                    surface_terrain=float(surface_terrain),
                    surface_bati=sbati,
                    nb_pieces=pieces,
                    adresse=f"{idx} rue de l'Exemple {cp} {commune}",
                    commune=commune,
                    code_postal=cp,
                    code_commune=cc,
                    departement=dep,
                    latitude=lat + ti * 0.001,
                    longitude=lng + ti * 0.001,
                    parcelle=parcelle,
                    date_mutation=date,
                    dpe_classe=dpe,
                    description=description,
                    url=f"https://immobilier.pappers.fr/carte?parcelle={parcelle}",
                    raw={"nature": nature, "mock": True},
                )
            )
    return listings


class MockSource(ListingSource):
    name = "mock"
    label = "Démo (fixtures)"

    def __init__(self, dataset: list[NormalizedListing] | None = None) -> None:
        self._dataset = dataset if dataset is not None else _build_dataset()

    @property
    def available(self) -> bool:
        return True

    def search(self, criteria: SearchCriteria) -> SearchResult:
        filtered = [item for item in self._dataset if matches(annotate(item), criteria)]
        total = len(filtered)
        start = (criteria.page - 1) * criteria.par_page
        page_items = filtered[start : start + criteria.par_page]
        return SearchResult(items=page_items, total=total, curseur_suivant=None, credits_estimes=0)

    def get(self, external_id: str, bases: list[str] | None = None) -> NormalizedListing | None:
        for item in self._dataset:
            if item.external_id == external_id:
                return item
        return None
