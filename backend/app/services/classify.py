"""Classification de l'état d'un bien à partir du texte des annonces.

Une ruine et un bien « à rénover » n'impliquent pas le même volume de travaux :
on utilise une échelle ordinale (du plus léger au plus lourd) et on retient le
niveau le plus sévère mentionné, en neutralisant d'abord les négations
(« aucun travaux », « sans travaux »...).
"""

from __future__ import annotations

import re
import unicodedata

# Échelle ordinale : niveau croissant de travaux.
HABITABLE = "habitable"
RAFRAICHIR = "rafraichir"
RENOVER = "renover"
GROS_TRAVAUX = "gros_travaux"
RUINE = "ruine"

CONDITIONS = [HABITABLE, RAFRAICHIR, RENOVER, GROS_TRAVAUX, RUINE]
NIVEAU = {HABITABLE: 0, RAFRAICHIR: 1, RENOVER: 2, GROS_TRAVAUX: 3, RUINE: 4}

# Mots-clés par niveau (texte normalisé : sans accents, minuscules).
# Ordre de balayage = du plus sévère au moins sévère.
_KEYWORDS: list[tuple[str, list[str]]] = [
    (
        RUINE,
        [
            "ruine",
            "ruines",
            "vestiges",
            "a reconstruire",
            "a demolir",
            "sans toiture",
            "sans toit",
            "effondre",
            "ecroule",
            "insalubre",
            "hors d'eau hors d'air a refaire",
        ],
    ),
    (
        GROS_TRAVAUX,
        [
            "gros travaux",
            "gros oeuvre",
            "rehabilitation lourde",
            "renovation lourde",
            "renovation complete",
            "renovation totale",
            "a rehabiliter",
            "a restaurer entierement",
            "tout a refaire",
            "travaux importants",
        ],
    ),
    (
        RENOVER,
        [
            "a renover",
            "a restaurer",
            "renovation",
            "rehabilitation",
            "travaux a prevoir",
            "travaux a realiser",
            "prevoir des travaux",
            "habitable apres travaux",
            "a moderniser",
            "besoin de travaux",
        ],
    ),
    (
        RAFRAICHIR,
        [
            "a rafraichir",
            "rafraichissement",
            "rafraichir",
            "quelques travaux",
            "travaux de decoration",
            "travaux de finition",
            "coup de peinture",
            "petits travaux",
            "a moderniser",
            "moderniser",
            "remettre au gout du jour",
            "a redecorer",
            "depoussierer",
        ],
    ),
    (
        HABITABLE,
        [
            "refait a neuf",
            "renove recemment",
            "recemment renove",
            "entierement renove",
            "renove",
            "renovee",
            "renoves",
            "renovees",
            "refait",
            "refaite",
            "aucun travaux",
            "sans travaux",
            "cle en main",
            "habitable de suite",
            "habitable immediatement",
            "etat impeccable",
            "impeccable",
            "tres bon etat",
            "bon etat",
            "parfait etat",
            "etat irreprochable",
            "pret a habiter",
            "prete a habiter",
            "rien a prevoir",
            "rien a faire",
            "neuf",
            "neuve",
            "construction recente",
            "recente construction",
            "maison recente",
            "contemporaine",
            "moderne",
            "aux normes",
        ],
    ),
]

# Phrases de négation à neutraliser avant analyse (évite les faux positifs).
_NEGATIONS = [
    "aucun travaux a prevoir",
    "aucun travaux",
    "sans travaux",
    "pas de travaux",
    "aucuns travaux",
    "ni travaux",
]


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    return re.sub(r"\s+", " ", text)


def classify(*parts: str | None) -> dict:
    """Analyse titre/description et renvoie {'condition': str|None, 'niveau_travaux': int|None}.

    `condition` vaut None si aucun indice n'est trouvé (état inconnu).
    """
    text = _normalize(" ".join(p for p in parts if p))
    if not text:
        return {"condition": None, "niveau_travaux": None}

    # Neutralise les négations : on remplace par un marqueur "habitable" explicite
    # pour éviter qu'un « aucun travaux à prévoir » ne déclenche « à rénover ».
    negated = False
    for neg in _NEGATIONS:
        if neg in text:
            negated = True
            text = text.replace(neg, " ")

    for condition, keywords in _KEYWORDS:
        if any(kw in text for kw in keywords):
            return {"condition": condition, "niveau_travaux": NIVEAU[condition]}

    if negated:
        return {"condition": HABITABLE, "niveau_travaux": NIVEAU[HABITABLE]}
    return {"condition": None, "niveau_travaux": None}
