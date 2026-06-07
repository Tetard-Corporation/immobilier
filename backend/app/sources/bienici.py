"""Connecteur Bien'ici (groupe Aviv) via son API JSON interne.

Bien'ici expose `realEstateAds.json?filters=<json>` (annonces structurées) et
`res.bienici.com/suggest.json?q=<lieu>` (résolution géographique).

Filtres poussés côté serveur (validés empiriquement) : type de bien, prix, surface,
pagination. Le filtrage géographique fin et l'état (ruine/à rénover) sont appliqués
côté client sur les annonces normalisées.
"""

from __future__ import annotations

import json

import httpx

from ..schemas import SearchCriteria
from ..services.enrich import annotate
from ..services.filters import matches
from .base import NormalizedListing, SearchResult
from .scraper import ScraperSource

_SUGGEST_URL = "https://res.bienici.com/suggest.json"

# Vocabulaire app -> propertyType Bien'ici.
_PROPERTY_MAP = {
    "terrain": "terrain",
    "maison": "house",
    "appartement": "flat",
    "immeuble": "building",
    "local_commercial": "shop",
    "parking": "parking",
}
# Inverse pour la normalisation (programme = lotissement/neuf, assimilé terrain).
_PROPERTY_MAP_REV = {
    "house": "maison",
    "flat": "appartement",
    "building": "immeuble",
    "shop": "local_commercial",
    "parking": "parking",
    "terrain": "terrain",
    "programme": "terrain",
}


def _scalar(value):
    """Bien'ici renvoie parfois une fourchette [min, max] ; on prend la borne basse."""
    if isinstance(value, list):
        nums = [v for v in value if isinstance(v, (int, float))]
        return min(nums) if nums else None
    return value if isinstance(value, (int, float)) else None


class BienIciSource(ScraperSource):
    name = "bienici"
    label = "Bien'ici"
    base_url = "https://www.bienici.com"

    # -- géo ----------------------------------------------------------------- #
    def _reverse_commune(self, lat: float, lon: float) -> str | None:
        """Nom de la commune d'un point (reverse-geocoding BAN) pour cibler la zone."""
        try:
            resp = httpx.get(
                "https://api-adresse.data.gouv.fr/reverse/",
                params={"lat": lat, "lon": lon, "type": "municipality"},
                timeout=self._settings.http_timeout_seconds,
            )
            resp.raise_for_status()
            feats = resp.json().get("features") or []
            return feats[0]["properties"].get("city") if feats else None
        except Exception:
            return None

    def _suggest(self, query: str) -> list[dict]:
        try:
            resp = self._get(_SUGGEST_URL, params={"q": query})
            data = resp.json()
        except Exception:
            return []
        return data if isinstance(data, list) else data.get("items", [])

    def _resolve_zone(self, query: str) -> dict | None:
        items = self._suggest(query)
        return items[0] if items else None

    @staticmethod
    def _item_center(item: dict) -> tuple[float, float] | None:
        bb = item.get("boundingBox") or {}
        try:
            return (bb["south"] + bb["north"]) / 2, (bb["west"] + bb["east"]) / 2
        except (KeyError, TypeError):
            return None

    def _zone_ids_sector(self, query: str, lat: float | None, lon: float | None, radius_km: float | None) -> list[str]:
        """Zones des communes d'un secteur : suggest par nom, filtré par distance au point.

        Bien'ici ne gère pas les bounding-box : on agrège les zoneIds des communes
        renvoyées par le suggest. Si un point + rayon sont fournis, on ne garde que les
        communes dont le centre tombe dans le rayon (élimine les homonymes lointains :
        'Bauges' -> Bourges/Bruges).
        """
        from ..services.geo import haversine_km

        ids: list[str] = []
        for item in self._suggest(query):
            if item.get("type") not in (None, "city") or not item.get("zoneIds"):
                continue
            if lat is not None and lon is not None and radius_km:
                center = self._item_center(item)
                if center is None or haversine_km(lat, lon, center[0], center[1]) > radius_km:
                    continue
            ids.extend(z for z in item["zoneIds"] if z not in ids)
        return ids[:30]

    # -- recherche exhaustive par petit rayon (évite le plafond des 100 récentes) ---- #
    _zone_cache: dict[str, str | None] = {}

    def _commune_zone_id(self, commune: dict) -> str | None:
        """Résout le zoneId Bien'ici d'une commune via suggest, désambiguïsé par proximité."""
        from ..services.geo import haversine_km

        code = commune.get("code")
        if code in self._zone_cache:
            return self._zone_cache[code]
        best, best_d = None, 1e9
        for item in self._suggest(commune["nom"]):
            if item.get("type") not in (None, "city") or not item.get("zoneIds"):
                continue
            ctr = self._item_center(item)
            if ctr is None:
                continue
            d = haversine_km(commune["lat"], commune["lon"], ctr[0], ctr[1])
            if d < best_d:
                best_d, best = d, item["zoneIds"][0]
        zid = best if best_d <= 8 else None  # rejette les homonymes lointains
        self._zone_cache[code] = zid
        return zid

    def zone_ids_around(self, lat: float, lon: float, radius_km: float, depts: list[str]) -> list[str]:
        """zoneIds de TOUTES les communes du rayon (pas seulement l'homonyme du pivot)."""
        from ..services.geo_communes import communes_within

        ids: list[str] = []
        for c in communes_within(lat, lon, radius_km, depts):
            zid = self._commune_zone_id(c)
            if zid and zid not in ids:
                ids.append(zid)
        return ids

    def search_zones(self, criteria: SearchCriteria, zone_ids: list[str], max_pages: int = 6) -> list[NormalizedListing]:
        """Récupère TOUTES les annonces d'un jeu de zones (paginé jusqu'au total)."""
        base = self._build_filters(criteria)
        base["zoneIdsByTypes"] = {"zoneIds": zone_ids}
        items, page = [], 0
        while page < max_pages:
            f = dict(base, **{"from": page * 100, "size": 100})
            resp = self._get("/realEstateAds.json", params={"filters": json.dumps(f)})
            data = resp.json()
            ads = data.get("realEstateAds") or []
            items.extend(annotate(self._normalize(ad)) for ad in ads)
            page += 1
            if not ads or page * 100 >= (data.get("total") or 0):
                break
        return [it for it in items if matches(it, criteria)]

    def collect_around(self, criteria: SearchCriteria, lat: float, lon: float, depts: list[str],
                       radii: tuple[float, ...] = (8, 16, 25, 35), cap: int | None = None) -> list[NormalizedListing]:
        """Collecte progressive : petit rayon puis on étend, en dédupliquant. Exhaustif par zone.

        Filtre par CODE COMMUNE (et non par coordonnées d'affichage, parfois floutées par
        Bien'ici), donc immunisé contre les annonces dont la géoloc est approximée.
        """
        from ..services.geo_communes import communes_within

        seen: set[str] = set()
        out: list[NormalizedListing] = []
        for r in radii:
            communes = communes_within(lat, lon, r, depts)
            codes = {c["code"] for c in communes}
            zids: list[str] = []
            for c in communes:
                zid = self._commune_zone_id(c)
                if zid and zid not in zids:
                    zids.append(zid)
            if not zids:
                continue
            for it in self.search_zones(criteria, zids):
                if it.external_id in seen:
                    continue
                if it.code_commune in codes:
                    seen.add(it.external_id)
                    out.append(it)
            if cap and len(out) >= cap:
                break
        return out

    def _build_filters(self, c: SearchCriteria) -> dict:
        property_types = c.property_types or ["terrain", "maison", "appartement"]
        bi_types = sorted({_PROPERTY_MAP.get(t, "house") for t in property_types})
        filters: dict[str, object] = {
            "size": min(max(c.par_page, 1), 100),
            "from": (max(c.page, 1) - 1) * min(max(c.par_page, 1), 100),
            "filterType": "buy",
            "propertyType": bi_types,
            "onTheMarket": [True],
            "sortBy": "publicationDate",
            "sortOrder": "desc",
        }
        if c.prix_min is not None:
            filters["minPrice"] = c.prix_min
        if c.prix_max is not None:
            filters["maxPrice"] = c.prix_max
        # `minArea`/`maxArea` filtre la surface (≈ terrain pour les terrains).
        area_min = c.surface_terrain_min if c.surface_terrain_min is not None else c.surface_bati_min
        area_max = c.surface_terrain_max if c.surface_terrain_max is not None else c.surface_bati_max
        if area_min is not None:
            filters["minArea"] = area_min
        if area_max is not None:
            filters["maxArea"] = area_max
        if c.nb_pieces_min is not None:
            filters["minRooms"] = c.nb_pieces_min
        if c.price_decreased:
            filters["priceHasDecreased"] = [True]
        return filters

    # -- normalisation ------------------------------------------------------- #
    @staticmethod
    def _normalize(ad: dict) -> NormalizedListing:
        ad_id = str(ad.get("id") or ad.get("reference") or "")
        blur = ad.get("blurInfo") or {}
        pos = blur.get("position") or blur.get("centroid") or {}
        district = ad.get("district") or {}
        description = ad.get("description")
        # La classification (état, qualité/nature) est centralisée dans services.enrich ;
        # ici on ne pose que les drapeaux propres à la source.
        flags = {"price_decreased": bool(ad.get("priceHasDecreased"))}

        type_bien = _PROPERTY_MAP_REV.get(ad.get("propertyType"), ad.get("propertyType"))

        return NormalizedListing(
            source="bienici",
            external_id=ad_id,
            type_bien=type_bien,
            prix=_scalar(ad.get("price")),
            surface_terrain=_scalar(ad.get("landSurfaceArea")),
            surface_bati=_scalar(ad.get("surfaceArea")),
            nb_pieces=_scalar(ad.get("roomsQuantity")),
            nb_chambres=_scalar(ad.get("bedroomsQuantity")),
            adresse=ad.get("title"),
            commune=ad.get("city"),
            code_postal=ad.get("postalCode"),
            code_commune=district.get("code_insee") or district.get("insee_code"),
            departement=ad.get("departmentCode"),
            latitude=pos.get("lat"),
            longitude=pos.get("lon"),
            parcelle=None,
            date_mutation=(ad.get("publicationDate") or ad.get("modificationDate") or "")[:10] or None,
            dpe_classe=(ad.get("energyClassification") or None),
            url=f"https://www.bienici.com/annonce/{ad_id}" if ad_id else None,
            description=description,
            flags=flags,
            raw=ad,
        )

    # -- API ----------------------------------------------------------------- #
    def search(self, criteria: SearchCriteria) -> SearchResult:
        filters = self._build_filters(criteria)

        # Recherche par secteur : nom de secteur (criteria.secteur) ou commune du point,
        # agrégée sur les communes proches puis affinée par le rayon.
        radius_km = (criteria.distance / 1000.0) if criteria.distance else None
        sector_query = criteria.secteur
        if not sector_query and criteria.latitude is not None and criteria.longitude is not None:
            sector_query = self._reverse_commune(criteria.latitude, criteria.longitude)

        if sector_query and (radius_km or criteria.secteur):
            zone_ids = self._zone_ids_sector(sector_query, criteria.latitude, criteria.longitude, radius_km)
            if zone_ids:
                filters["zoneIdsByTypes"] = {"zoneIds": zone_ids}
        else:
            # Sinon, ciblage par commune/CP/département (zone unique).
            geo_query = (
                criteria.code_postal or criteria.code_commune or criteria.departement or criteria.region
            )
            zone = self._resolve_zone(geo_query) if geo_query else None
            # Bien'ici attend les identifiants NUMÉRIQUES de zone (champ `zoneIds`).
            if zone and zone.get("zoneIds"):
                filters["zoneIdsByTypes"] = {"zoneIds": list(zone["zoneIds"])}

        resp = self._get("/realEstateAds.json", params={"filters": json.dumps(filters)})
        data = resp.json()
        ads = data.get("realEstateAds") or []
        server_total = data.get("total")

        items = [annotate(self._normalize(ad)) for ad in ads]
        # Filtrage côté client (géo précise, état, qualité/nature, surfaces bâti, etc.).
        filtered = [it for it in items if matches(it, criteria)]
        total = server_total if len(filtered) == len(items) else None
        return SearchResult(items=filtered, total=total, curseur_suivant=None, credits_estimes=0)

    def get(self, external_id: str, bases: list[str] | None = None) -> NormalizedListing | None:
        try:
            resp = self._get("/realEstateAd.json", params={"id": external_id})
        except Exception:
            return None
        data = resp.json()
        ad = data if isinstance(data, dict) and data.get("id") else None
        return self._normalize(ad) if ad else None
