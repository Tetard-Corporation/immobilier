"""Dédoublonnage inter-sources : empreinte canonique d'un bien.

Deux annonces du même bien sur des portails différents doivent partager la même
empreinte. On combine la géolocalisation arrondie (ou le code postal) et des
caractéristiques stables (surfaces arrondies, tranche de prix).
"""

from __future__ import annotations

import hashlib

from ..sources.base import NormalizedListing


def _round_or_none(value: float | None, base: int) -> int | None:
    if value is None:
        return None
    return int(round(value / base) * base)


def fingerprint(listing: NormalizedListing) -> str:
    """Empreinte stable d'un bien, indépendante de la source.

    Le prix est volontairement exclu (il diffère entre portails et dans le temps ;
    il est suivi séparément). On combine géo grossière + type + surfaces en paliers.
    """
    # Géo : ~110 m de tolérance (3 décimales) si dispo, sinon code postal/commune.
    if listing.latitude is not None and listing.longitude is not None:
        geo = f"{listing.latitude:.3f},{listing.longitude:.3f}"
    else:
        geo = f"{listing.code_postal or ''}|{listing.code_commune or ''}|{listing.commune or ''}"

    parts = [
        (listing.type_bien or "").lower(),
        geo,
        str(_round_or_none(listing.surface_terrain, 50) or ""),
        str(_round_or_none(listing.surface_bati, 10) or ""),
    ]
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def dedupe(listings: list[NormalizedListing]) -> list[NormalizedListing]:
    """Conserve une annonce par empreinte (la moins chère / la plus complète)."""
    best: dict[str, NormalizedListing] = {}
    for item in listings:
        key = fingerprint(item)
        current = best.get(key)
        if current is None:
            best[key] = item
            continue
        # Priorité : prix le plus bas connu, puis description la plus longue.
        cur_price = current.prix if current.prix is not None else float("inf")
        new_price = item.prix if item.prix is not None else float("inf")
        if new_price < cur_price or (
            new_price == cur_price and len(item.description or "") > len(current.description or "")
        ):
            best[key] = item
    return list(best.values())
