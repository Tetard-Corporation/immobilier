"""Annotation centralisée des annonces : classification d'état + de qualité/nature.

Appliquée uniformément à toutes les sources dans le pipeline de recherche, à partir
du texte disponible (description, adresse/titre). Les flags propres à la source
(ex. price_decreased) déjà présents sont préservés.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .classify import classify
from .quality import classify_quality
from .scoring import compute_score

if TYPE_CHECKING:
    from ..sources.base import NormalizedListing


def annotate(item: "NormalizedListing") -> "NormalizedListing":
    flags = dict(item.flags or {})
    texts = [item.description, item.adresse]
    flags.update(classify(*texts))
    flags.update(classify_quality(*texts))

    # Score d'investissement (recalculé à partir des flags + signaux d'enrichissement).
    has_text = bool(item.description or item.adresse)
    result = compute_score(flags, has_text=has_text)
    flags["score"] = result.score
    flags["score_details"] = result.components

    item.flags = flags
    return item
