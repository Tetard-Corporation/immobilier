"""Classification de biens à partir du texte des annonces (ruines, à rénover)."""

from __future__ import annotations

import re
import unicodedata

# Mots-clés (recherchés sur texte normalisé sans accents, en minuscules).
_RUINE_KEYWORDS = [
    "ruine",
    "ruines",
    "corps de ferme",
    "grange a renover",
    "grange a rehabiliter",
    "batisse a restaurer",
    "a restaurer entierement",
    "tout a refaire",
    "vestiges",
    "ancien corps de ferme",
    "ruine a rehabiliter",
]

_A_RENOVER_KEYWORDS = [
    "a renover",
    "a rafraichir",
    "travaux a prevoir",
    "travaux a realiser",
    "renovation",
    "rehabilitation",
    "habitable apres travaux",
    "gros travaux",
    "a moderniser",
    "a restaurer",
    "prevoir des travaux",
]


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    return re.sub(r"\s+", " ", text)


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(kw in text for kw in keywords)


def classify(*parts: str | None) -> dict:
    """Analyse titre/description et renvoie {'ruine': bool, 'a_renover': bool}."""
    text = _normalize(" ".join(p for p in parts if p))
    is_ruine = _contains_any(text, _RUINE_KEYWORDS)
    # Une ruine est, de fait, "à rénover".
    is_a_renover = is_ruine or _contains_any(text, _A_RENOVER_KEYWORDS)
    return {"ruine": is_ruine, "a_renover": is_a_renover}
