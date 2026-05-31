"""Connecteur Paruvendu (paruvendu.fr).

Site accessible et rendu côté serveur (pas d'anti-bot bloquant constaté). Les
annonces sont des cartes `blocAnnonce` ; on les parse directement depuis le HTML.
Filtres prix/surface/géo appliqués côté client sur les annonces normalisées.
"""

from __future__ import annotations

import html as _html
import re

from ..schemas import SearchCriteria
from ..services.enrich import annotate
from ..services.filters import matches
from .base import NormalizedListing, SearchResult
from .scraper import ScraperSource

_SLUG = {
    "terrain": "terrain",
    "maison": "maison",
    "appartement": "appartement",
    "immeuble": "immeuble",
    "local_commercial": "local-commercial",
    "parking": "parking-box",
}

_CARD_START = re.compile(r'<div\s+class="blocAnnonce')
_ID = re.compile(r'data-id="(\d+)"')
_HREF = re.compile(r'href="(/immobilier/[^"]+)"')
_TITLE = re.compile(r'<a[^>]*\btitle="([^"]*)"')
_PRICE = re.compile(r"(\d[\d\s  ]{2,})\s*(?:€|&euro;|&#8364;)")
_SURFACE = re.compile(r"(\d[\d\s  ]*)\s*m(?:²|&sup2;|\s*2|&#178;)", re.I)
_LOC = re.compile(r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-.\s]{1,40}?)\s*\((\d{2,3})\)")
_TAGS = re.compile(r"<[^>]+>")


def _num(raw: str | None) -> float | None:
    if not raw:
        return None
    cleaned = re.sub(r"[\s ]", "", raw)
    try:
        return float(cleaned)
    except ValueError:
        return None


class ParuvenduSource(ScraperSource):
    name = "paruvendu"
    label = "Paruvendu"
    base_url = "https://www.paruvendu.fr"

    def _path(self, c: SearchCriteria) -> str:
        types = c.property_types or ["terrain"]
        slug = next((_SLUG[t] for t in types if t in _SLUG), "immobilier")
        page = max(c.page, 1)
        suffix = f"?p={page}" if page > 1 else ""
        return f"/immobilier/vente/{slug}/{suffix}"

    def _type_hint(self, c: SearchCriteria) -> str | None:
        for t in ("terrain", "maison", "appartement", "immeuble", "local_commercial", "parking"):
            if t in (c.property_types or []):
                return t
        return None

    def _cards(self, html: str) -> list[str]:
        starts = [m.start() for m in _CARD_START.finditer(html)]
        return [html[s : (starts[i + 1] if i + 1 < len(starts) else s + 6000)] for i, s in enumerate(starts)]

    def _parse_card(self, card: str, type_hint: str | None) -> NormalizedListing | None:
        ad_id = _ID.search(card)
        if not ad_id:
            return None
        href = _HREF.search(card)
        text = _html.unescape(_TAGS.sub(" ", card))
        text = re.sub(r"\s+", " ", text)
        title = _html.unescape(_TITLE.search(card).group(1)) if _TITLE.search(card) else ""

        price = _PRICE.search(text)
        # Surface prioritairement depuis le titre (plus fiable que le corps).
        surface_m = _SURFACE.search(title) or _SURFACE.search(text)
        surface = _num(surface_m.group(1)) if surface_m else None
        loc = _LOC.search(text)

        prix = _num(price.group(1)) if price else None
        # Écarte un éventuel prix au m² (faible) capté avant le prix total.
        if prix is not None and prix < 1000:
            prix = None

        return NormalizedListing(
            source="paruvendu",
            external_id=ad_id.group(1),
            type_bien=type_hint,
            prix=prix,
            surface_terrain=surface if type_hint == "terrain" else None,
            surface_bati=surface if type_hint not in ("terrain", None) else None,
            adresse=loc.group(1).strip() if loc else (title or None),
            commune=loc.group(1).strip() if loc else None,
            departement=loc.group(2) if loc else None,
            url=self.base_url + href.group(1) if href else None,
            description=title or None,
            flags={},
            raw={"title": title, "text": text[:500]},
        )

    def search(self, criteria: SearchCriteria) -> SearchResult:
        resp = self._get(self._path(criteria))
        type_hint = self._type_hint(criteria)
        items = []
        for card in self._cards(resp.text):
            parsed = self._parse_card(card, type_hint)
            if parsed:
                items.append(annotate(parsed))
        filtered = [it for it in items if matches(it, criteria)]
        return SearchResult(items=filtered, total=None, curseur_suivant=None, credits_estimes=0)

    def get(self, external_id: str, bases: list[str] | None = None) -> NormalizedListing | None:
        return None
