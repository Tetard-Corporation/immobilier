"""Annotation centralisée des annonces : complétion des champs + classification.

Appliquée uniformément à toutes les sources dans le pipeline de recherche, à partir
du texte disponible (description, adresse/titre). Les flags propres à la source
(ex. price_decreased) déjà présents sont préservés.

Trois passes, dans cet ordre :

1. **Complétion** (`completion.completer`) : les champs structurels que la source n'a pas
   donnés — chambres, pièces, terrain, surface habitable — sont lus dans le texte. Elle
   vient EN PREMIER parce que tout ce qui suit s'en sert : le filtrage par critères, le
   score d'investissement, et les préférences du set.
2. **Classification** de l'état du bâti et de la qualité/nature du terrain.
3. **Score d'investissement**, recalculé à partir des flags et des champs complétés.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .classify import classify
from .completion import completer
from .quality import classify_quality
from .scoring import compute_score

if TYPE_CHECKING:
    from ..sources.base import NormalizedListing


def annotate(item: "NormalizedListing") -> "NormalizedListing":
    flags = dict(item.flags or {})
    # Complète AVANT de scorer : un terrain lu dans le texte doit compter dans le score,
    # sinon la complétion n'aurait d'effet que sur l'affichage.
    completes = completer(item)
    if completes:
        # Provenance : ces champs viennent du TEXTE, pas de la source. Le drapeau reste
        # dans l'objet annoté (les collecteurs peuvent le journaliser) ; `upsert_listing`
        # ne le persiste pas — la base ne garde que la valeur.
        flags["champs_completes"] = sorted(completes)

    texts = [item.description, item.adresse]
    flags.update(classify(*texts))
    flags.update(classify_quality(*texts))

    # Score d'investissement (piliers/sous-piliers), recalculé à partir des flags.
    ctx = {
        "has_text": bool(item.description or item.adresse),
        "surface_terrain": item.surface_terrain,
        "surface_bati": item.surface_bati,
        "type_bien": item.type_bien,
        "prix": item.prix,
    }
    result = compute_score(flags, ctx)
    flags["score"] = result.score
    flags["score_details"] = result.pillars

    item.flags = flags
    return item
