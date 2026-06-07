"""Connecteur SeLoger (groupe Aviv).

⚠️ Protégé par Datadome : l'accès échoue sans proxy/headless. Construction de la
requête (`list.htm`) + parsing JSON-LD prioritaire, à valider en live. Géo fine
filtrée côté client.
"""

from __future__ import annotations

import re

import httpx

from ..schemas import SearchCriteria
from ..services.enrich import annotate
from ..services.filters import matches
from .base import NormalizedListing, SearchResult
from .htmlutil import json_ld_items, realestate_fields
from .scraper import ScraperBlocked, ScraperSource

# property_type -> code "types" SeLoger.
_TYPE_CODE = {"appartement": "1", "maison": "2", "terrain": "4", "immeuble": "9", "parking": "7"}
_ID_RE = re.compile(r"(\d{6,})")


def _external_id(url: str | None, name: str | None) -> str:
    if url:
        m = _ID_RE.search(url)
        if m:
            return m.group(1)
        return url.rstrip("/").rsplit("/", 1)[-1]
    return str(abs(hash(name or "")))


class SeLogerSource(ScraperSource):
    name = "seloger"
    label = "SeLoger"
    base_url = "https://www.seloger.com"

    def _params(self, c: SearchCriteria) -> dict:
        property_types = c.property_types or ["terrain", "maison", "appartement"]
        codes = sorted({_TYPE_CODE[t] for t in property_types if t in _TYPE_CODE})
        params: dict[str, str] = {
            "projects": "2",  # achat
            "types": ",".join(codes),
            "natures": "1,2,4",  # ancien, neuf, viager
            "enableGoogleStructuredData": "true",
        }
        if c.prix_min is not None or c.prix_max is not None:
            lo = int(c.prix_min) if c.prix_min is not None else "NaN"
            hi = int(c.prix_max) if c.prix_max is not None else "NaN"
            params["price"] = f"{lo}/{hi}"
        if c.surface_terrain_min is not None or c.surface_terrain_max is not None:
            lo = int(c.surface_terrain_min) if c.surface_terrain_min is not None else "NaN"
            hi = int(c.surface_terrain_max) if c.surface_terrain_max is not None else "NaN"
            params["landSurface"] = f"{lo}/{hi}"
        return params

    def _type_hint(self, c: SearchCriteria) -> str | None:
        for t in ("terrain", "maison", "appartement", "immeuble"):
            if t in (c.property_types or []):
                return t
        return None

    def _fetch_html(self, params: dict) -> str:
        try:
            return self._get("/list.htm", params=params).text
        except (ScraperBlocked, httpx.HTTPError):
            query = "&".join(f"{k}={v}" for k, v in params.items())
            return self._fetch_headless(f"{self.base_url}/list.htm?{query}")

    def _parse(self, html: str, type_hint: str | None) -> list[NormalizedListing]:
        items: list[NormalizedListing] = []
        for obj in json_ld_items(html):
            f = realestate_fields(obj)
            if not f or f.get("price") is None:
                continue
            surface = f.get("surface")
            items.append(
                NormalizedListing(
                    source="seloger",
                    external_id=_external_id(f.get("url"), f.get("name")),
                    type_bien=type_hint,
                    prix=f.get("price"),
                    surface_terrain=surface if type_hint == "terrain" else None,
                    surface_bati=surface if type_hint != "terrain" else None,
                    adresse=f.get("street") or f.get("name"),
                    commune=f.get("city"),
                    code_postal=f.get("postal_code"),
                    latitude=f.get("latitude"),
                    longitude=f.get("longitude"),
                    url=f.get("url"),
                    description=f.get("description"),
                    flags={},
                    raw=obj,
                )
            )
        return items

    def search(self, criteria: SearchCriteria) -> SearchResult:
        html = self._fetch_html(self._params(criteria))
        items = [annotate(it) for it in self._parse(html, self._type_hint(criteria))]
        filtered = [it for it in items if matches(it, criteria)]
        return SearchResult(items=filtered, total=None, curseur_suivant=None, credits_estimes=0)

    def get(self, external_id: str, bases: list[str] | None = None) -> NormalizedListing | None:
        return None
