"""Connecteur PAP (pap.fr — de particulier à particulier).

⚠️ PAP est protégé par Cloudflare ("Just a moment...") : l'accès direct échoue sans
navigateur (mode headless) et souvent un proxy. Le parsing s'appuie en priorité sur
le JSON-LD de la page (robuste) ; à valider/affiner en live dans un environnement
proxifié. Le filtrage géographique fin est appliqué côté client.
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

# property_type -> segment d'URL PAP (priorité au plus spécifique).
_SLUG = {
    "terrain": "vente-terrains",
    "maison": "vente-maisons",
    "appartement": "vente-appartements",
    "immeuble": "vente-immeubles",
}
_ID_RE = re.compile(r"(\d{5,})")


def _external_id(url: str | None, name: str | None) -> str:
    if url:
        m = _ID_RE.search(url)
        if m:
            return m.group(1)
        return url.rstrip("/").rsplit("/", 1)[-1]
    return str(abs(hash(name or "")))


class PapSource(ScraperSource):
    name = "pap"
    label = "PAP"
    base_url = "https://www.pap.fr"

    def _path(self, c: SearchCriteria) -> str:
        types = c.property_types or ["terrain"]
        for t in ("terrain", "maison", "appartement", "immeuble"):
            if t in types:
                return f"/annonce/{_SLUG[t]}"
        return "/annonce/vente-immobilier"

    def _type_hint(self, c: SearchCriteria) -> str | None:
        types = c.property_types or []
        for t in ("terrain", "maison", "appartement", "immeuble"):
            if t in types:
                return t
        return None

    def _fetch_html(self, path: str) -> str:
        # Tentative HTTP directe puis repli headless (Cloudflare ou erreur réseau).
        try:
            return self._get(path).text
        except (ScraperBlocked, httpx.HTTPError):
            return self._fetch_headless(self.base_url + path)

    def _parse(self, html: str, type_hint: str | None) -> list[NormalizedListing]:
        items: list[NormalizedListing] = []
        for obj in json_ld_items(html):
            f = realestate_fields(obj)
            if not f or f.get("price") is None:
                continue
            surface = f.get("surface")
            items.append(
                NormalizedListing(
                    source="pap",
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
                    date_mutation=None,
                    url=f.get("url"),
                    description=f.get("description"),
                    flags={},
                    raw=obj,
                )
            )
        return items

    def search(self, criteria: SearchCriteria) -> SearchResult:
        html = self._fetch_html(self._path(criteria))
        items = [annotate(it) for it in self._parse(html, self._type_hint(criteria))]
        filtered = [it for it in items if matches(it, criteria)]
        return SearchResult(items=filtered, total=None, curseur_suivant=None, credits_estimes=0)

    def get(self, external_id: str, bases: list[str] | None = None) -> NormalizedListing | None:
        return None
