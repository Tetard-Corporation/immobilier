"""Connecteur Leboncoin via son API interne `finder/search`.

⚠️ Leboncoin est protégé par Datadome : les appels échouent sans cookie/proxy valides.
Ce connecteur nécessite, dans l'environnement d'exécution, un `PROXY_URL` (proxy
résidentiel) et/ou un cookie Datadome valide. La logique de construction de requête
et de parsing est testée hors-ligne (fixtures) ; elle peut nécessiter un ajustement
si Leboncoin fait évoluer son schéma.
"""

from __future__ import annotations

from ..schemas import SearchCriteria
from ..services.enrich import annotate
from ..services.filters import matches
from .base import NormalizedListing, SearchResult
from .scraper import ScraperSource

# Catégorie 9 = Ventes immobilières.
_CATEGORY_VENTES = "9"

# real_estate_type Leboncoin -> vocabulaire app, et inverse.
_RET_TO_APP = {"1": "maison", "2": "appartement", "3": "terrain", "4": "parking", "5": "local_commercial"}
_APP_TO_RET = {
    "maison": "1",
    "appartement": "2",
    "terrain": "3",
    "parking": "4",
    "local_commercial": "5",
    "immeuble": "5",
}


def _attrs(ad: dict) -> dict:
    """Aplati la liste d'attributs Leboncoin en dict {key: value}."""
    out: dict[str, str] = {}
    for a in ad.get("attributes") or []:
        if isinstance(a, dict) and a.get("key") is not None:
            out[a["key"]] = a.get("value")
    return out


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class LeboncoinSource(ScraperSource):
    name = "leboncoin"
    label = "Leboncoin"
    base_url = "https://api.leboncoin.fr"

    def _build_payload(self, c: SearchCriteria) -> dict:
        property_types = c.property_types or ["terrain", "maison", "appartement"]
        ret = sorted({_APP_TO_RET[p] for p in property_types if p in _APP_TO_RET})

        ranges: dict[str, dict] = {}
        if c.prix_min is not None or c.prix_max is not None:
            ranges["price"] = {k: v for k, v in (("min", c.prix_min), ("max", c.prix_max)) if v is not None}
        if c.surface_terrain_min is not None or c.surface_terrain_max is not None:
            ranges["land_plot_surface"] = {
                k: v for k, v in (("min", c.surface_terrain_min), ("max", c.surface_terrain_max)) if v is not None
            }
        if c.surface_bati_min is not None or c.surface_bati_max is not None:
            ranges["square"] = {
                k: v for k, v in (("min", c.surface_bati_min), ("max", c.surface_bati_max)) if v is not None
            }
        if c.nb_pieces_min is not None or c.nb_pieces_max is not None:
            ranges["rooms"] = {
                k: v for k, v in (("min", c.nb_pieces_min), ("max", c.nb_pieces_max)) if v is not None
            }

        filters: dict = {
            "category": {"id": _CATEGORY_VENTES},
            "enums": {"ad_type": ["offer"], "real_estate_type": ret},
            "ranges": ranges,
        }
        if c.code_postal:
            filters["location"] = {"city_zipcodes": [{"zipcode": c.code_postal}]}

        limit = min(max(c.par_page, 1), 100)
        return {
            "filters": filters,
            "limit": limit,
            "offset": (max(c.page, 1) - 1) * limit,
            "sort_by": "time",
            "sort_order": "desc",
        }

    @staticmethod
    def _normalize(ad: dict) -> NormalizedListing:
        at = _attrs(ad)
        loc = ad.get("location") or {}
        prices = ad.get("price")
        prix = _num(prices[0]) if isinstance(prices, list) and prices else _num(prices)
        ad_id = str(ad.get("list_id") or ad.get("id") or "")
        return NormalizedListing(
            source="leboncoin",
            external_id=ad_id,
            type_bien=_RET_TO_APP.get(str(at.get("real_estate_type")), None),
            prix=prix,
            surface_terrain=_num(at.get("land_plot_surface")),
            surface_bati=_num(at.get("square")),
            nb_pieces=int(_num(at.get("rooms"))) if _num(at.get("rooms")) else None,
            adresse=ad.get("subject"),
            commune=loc.get("city"),
            code_postal=loc.get("zipcode"),
            code_commune=loc.get("city_label"),
            departement=loc.get("department_id") or loc.get("department_name"),
            latitude=_num(loc.get("lat")),
            longitude=_num(loc.get("lng")),
            parcelle=None,
            date_mutation=(ad.get("first_publication_date") or "")[:10] or None,
            dpe_classe=at.get("energy_rate"),
            url=ad.get("url"),
            description=ad.get("body"),
            flags={},
            raw=ad,
        )

    def search(self, criteria: SearchCriteria) -> SearchResult:
        payload = self._build_payload(criteria)
        resp = self._post("/finder/search", json_body=payload)
        data = resp.json()
        ads = data.get("ads") or []
        items = [annotate(self._normalize(ad)) for ad in ads]
        filtered = [it for it in items if matches(it, criteria)]
        total = data.get("total") if len(filtered) == len(items) else None
        return SearchResult(items=filtered, total=total, curseur_suivant=None, credits_estimes=0)

    def get(self, external_id: str, bases: list[str] | None = None) -> NormalizedListing | None:
        try:
            resp = self._get(f"/api/adview/v1/items/{external_id}")
        except Exception:
            return None
        data = resp.json()
        ad = data if isinstance(data, dict) and (data.get("list_id") or data.get("id")) else None
        return annotate(self._normalize(ad)) if ad else None
