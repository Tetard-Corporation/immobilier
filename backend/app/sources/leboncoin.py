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
from ..services.geo_communes import code_insee
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


# Au-delà, c'est un code d'absence et non un logement. Large exprès : la plus grande
# maison du catalogue en déclare 16.
_CHAMBRES_MAX = 30


def _chambres_plausibles(n: float | None) -> int | None:
    return int(n) if n and 0 < n <= _CHAMBRES_MAX else None


class LeboncoinSource(ScraperSource):
    name = "leboncoin"
    label = "Leboncoin"
    base_url = "https://api.leboncoin.fr"

    @property
    def available(self) -> bool:
        # Datadome bloque les IP datacenter : sans proxy résidentiel NI cookie Datadome,
        # tout appel renvoie 403. On évite de gaspiller des appels en se déclarant
        # indisponible tant qu'aucun des deux n'est configuré.
        return bool(self._settings.proxy_url or self._settings.leboncoin_datadome)

    def _headers(self) -> dict:
        """En-têtes attendus par l'API Leboncoin (api_key obligatoire + cookie Datadome)."""
        h = {
            "api_key": self._settings.leboncoin_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "LBC;Android;13;Pixel;native;...;wifi;phone",
        }
        if self._settings.leboncoin_datadome:
            h["Cookie"] = f"datadome={self._settings.leboncoin_datadome}"
        return h

    @staticmethod
    def _range(lo, hi) -> dict:
        # L'API Leboncoin exige des ENTIERS dans les `ranges` : une borne float
        # (ex. prix_max=300000.0 côté SearchCriteria) renvoie 400 Bad Request.
        return {k: int(v) for k, v in (("min", lo), ("max", hi)) if v is not None}

    def _build_payload(self, c: SearchCriteria) -> dict:
        property_types = c.property_types or ["terrain", "maison", "appartement"]
        ret = sorted({_APP_TO_RET[p] for p in property_types if p in _APP_TO_RET})

        ranges: dict[str, dict] = {}
        if c.prix_min is not None or c.prix_max is not None:
            ranges["price"] = self._range(c.prix_min, c.prix_max)
        if c.surface_terrain_min is not None or c.surface_terrain_max is not None:
            ranges["land_plot_surface"] = self._range(c.surface_terrain_min, c.surface_terrain_max)
        if c.surface_bati_min is not None or c.surface_bati_max is not None:
            ranges["square"] = self._range(c.surface_bati_min, c.surface_bati_max)
        if c.nb_pieces_min is not None or c.nb_pieces_max is not None:
            ranges["rooms"] = self._range(c.nb_pieces_min, c.nb_pieces_max)

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
            # `bedrooms` est publié par Leboncoin sur 78 % de ses annonces immobilières
            # et n'était pas lu : les 2 698 biens leboncoin de la base sont TOUS entrés
            # sans nombre de chambres, et le set têtard leur appliquait alors son repli
            # « pièces - 1 » — exact 46 % du temps, et surestimé une fois sur deux.
            # `999999` y sert de « non renseigné » (vu sur une maison de 9 pièces à
            # Auris) : entrée telle quelle, la valeur satisfait tous les seuils de
            # chambres et s'affiche sur la carte du bien. On la laisse à la porte, et le
            # repli « pièces - 1 » fait son travail comme pour une annonce muette.
            nb_chambres=_chambres_plausibles(_num(at.get("bedrooms"))),
            adresse=ad.get("subject"),
            commune=loc.get("city"),
            code_postal=loc.get("zipcode"),
            # `city_label` est un LIBELLÉ (« Chalencon 07240 »), pas un code INSEE — et le
            # modèle attend un code : la fibre y est indexée, le dédoublonnage s'en sert.
            # Mesuré avant correction : 1 430 biens leboncoin sur 1 580 sans fibre
            # résolue, contre 2 295/2 295 côté bienici.
            code_commune=code_insee(loc.get("city"), loc.get("zipcode")),
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
        resp = self._post("/finder/search", json_body=payload, headers=self._headers())
        data = resp.json()
        ads = data.get("ads") or []
        items = [annotate(self._normalize(ad)) for ad in ads]
        filtered = [it for it in items if matches(it, criteria)]
        total = data.get("total") if len(filtered) == len(items) else None
        return SearchResult(items=filtered, total=total, curseur_suivant=None, credits_estimes=0)

    def get(self, external_id: str, bases: list[str] | None = None) -> NormalizedListing | None:
        try:
            resp = self._get(f"/api/adview/v1/items/{external_id}", headers=self._headers())
        except Exception:
            return None
        data = resp.json()
        ad = data if isinstance(data, dict) and (data.get("list_id") or data.get("id")) else None
        return annotate(self._normalize(ad)) if ad else None
