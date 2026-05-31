"""Utilitaires d'extraction HTML (sans dépendance externe).

Stratégie privilégiée : lire les blocs JSON-LD (`application/ld+json`), standard
largement utilisé par les portails immobiliers et bien plus robuste que des
sélecteurs CSS. Un repli CSS (selectolax) est disponible de façon optionnelle.
"""

from __future__ import annotations

import html as _html
import json
import re

_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I,
)


def _flatten(data) -> list[dict]:
    out: list[dict] = []
    if isinstance(data, list):
        for d in data:
            out.extend(_flatten(d))
    elif isinstance(data, dict):
        graph = data.get("@graph")
        if isinstance(graph, list):
            for d in graph:
                out.extend(_flatten(d))
        else:
            out.append(data)
    return out


def json_ld_items(html: str | None) -> list[dict]:
    """Renvoie tous les objets JSON-LD trouvés dans la page (graphes aplatis)."""
    items: list[dict] = []
    for match in _LD_RE.finditer(html or ""):
        raw = match.group(1).strip()
        for candidate in (raw, _html.unescape(raw)):
            try:
                items.extend(_flatten(json.loads(candidate)))
                break
            except json.JSONDecodeError:
                continue
    return items


def has_type(obj: dict, *types: str) -> bool:
    """Vrai si l'objet JSON-LD a l'un des @type donnés (insensible à la casse)."""
    t = obj.get("@type")
    values = t if isinstance(t, list) else [t]
    wanted = {x.lower() for x in types}
    return any(isinstance(v, str) and v.lower() in wanted for v in values)


_REALESTATE_TYPES = (
    "RealEstateListing",
    "Residence",
    "House",
    "Apartment",
    "SingleFamilyResidence",
    "Product",
    "Offer",
    "Place",
    "Accommodation",
    "House",
)


def _to_float(value):
    try:
        return float(str(value).replace(",", ".").split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def realestate_fields(obj: dict) -> dict | None:
    """Extrait des champs immobiliers communs d'un objet JSON-LD, ou None."""
    if not isinstance(obj, dict) or not has_type(obj, *_REALESTATE_TYPES):
        return None

    offers = obj.get("offers")
    price = None
    if isinstance(offers, dict):
        price = offers.get("price") or (offers.get("priceSpecification") or {}).get("price")
    elif isinstance(offers, list) and offers and isinstance(offers[0], dict):
        price = offers[0].get("price")
    price = _to_float(price if price is not None else obj.get("price"))

    floor = obj.get("floorSize")
    surface = _to_float(floor.get("value")) if isinstance(floor, dict) else None

    address = obj.get("address")
    address = address if isinstance(address, dict) else {}
    geo = obj.get("geo")
    geo = geo if isinstance(geo, dict) else {}

    return {
        "name": obj.get("name"),
        "url": obj.get("url"),
        "description": obj.get("description"),
        "price": price,
        "surface": surface,
        "postal_code": address.get("postalCode"),
        "city": address.get("addressLocality"),
        "street": address.get("streetAddress"),
        "latitude": _to_float(geo.get("latitude")),
        "longitude": _to_float(geo.get("longitude")),
    }


def select(html: str, css: str):
    """Repli CSS via selectolax (dépendance optionnelle). Liste de nœuds."""
    try:
        from selectolax.parser import HTMLParser
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "selectolax requis pour le repli CSS : pip install -r requirements-scrapers.txt"
        ) from exc
    return HTMLParser(html).css(css)
