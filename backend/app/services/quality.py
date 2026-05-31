"""Classification de la qualité/nature d'un bien à partir du texte des annonces.

Axe distinct de l'état du bâti (classify.py) : on détecte des aménités positives
(vue, forêt, eau, calme, isolement, sans vis-à-vis...) et des nuisances négatives
(vis-à-vis, route/voie ferrée, mitoyenneté...), puis on en déduit un score et un
drapeau « nature d'exception ».
"""

from __future__ import annotations

import re
import unicodedata

# Aménités positives. Les "fortes" suffisent à elles seules à marquer le caractère nature.
VUE = "vue"
FORET = "foret"
EAU = "eau"
CALME = "calme"
ISOLE = "isole"
SANS_VIS_A_VIS = "sans_vis_a_vis"
ARBORE = "arbore"
ENSOLEILLE = "ensoleille"

FEATURES = [VUE, FORET, EAU, CALME, ISOLE, SANS_VIS_A_VIS, ARBORE, ENSOLEILLE]
_STRONG = {VUE, FORET, EAU, ISOLE}

# Nuisances négatives.
VIS_A_VIS = "vis_a_vis"
NUISANCES = "nuisances"
MITOYEN = "mitoyen"

_POSITIVE_KEYWORDS: dict[str, list[str]] = {
    VUE: [
        "vue degagee",
        "vue panoramique",
        "vue imprenable",
        "vue exceptionnelle",
        "vue mer",
        "vue sur mer",
        "vue montagne",
        "vue sur la montagne",
        "vue sur la vallee",
        "belle vue",
        "magnifique vue",
        "point de vue",
        "vue dominante",
    ],
    FORET: [
        "foret",
        "en lisiere de foret",
        "boise",
        "sous-bois",
        "sous bois",
        "arbres centenaires",
        "bordure de bois",
    ],
    EAU: [
        "riviere",
        "ruisseau",
        "cours d'eau",
        "etang",
        "plan d'eau",
        "bord de l'eau",
        "au bord de l'eau",
        "lac",
        "source",
        "acces lac",
        "pieds dans l'eau",
    ],
    CALME: ["au calme", "tres calme", "paisible", "tranquille", "quietude", "havre de paix"],
    ISOLE: [
        "isole",
        "en pleine nature",
        "en pleine campagne",
        "sans voisinage",
        "sans vis a vis ni voisinage",
        "retire",
        "loin de tout",
        "au coeur de la nature",
        "ecart du village",
    ],
    SANS_VIS_A_VIS: ["sans vis-a-vis", "aucun vis-a-vis", "sans aucun vis-a-vis"],
    ARBORE: ["arbore", "verdoyant", "paysager", "parc arbore", "terrain arbore", "joliment plante"],
    ENSOLEILLE: ["plein sud", "exposition sud", "tres ensoleille", "tres lumineux", "baigne de lumiere"],
}

_NEGATIVE_KEYWORDS: dict[str, list[str]] = {
    VIS_A_VIS: ["vis-a-vis"],  # neutralisé si "sans/aucun vis-à-vis" (voir _strip_negated)
    NUISANCES: [
        "proximite autoroute",
        "bord de route",
        "bord de nationale",
        "route passante",
        "route frequentee",
        "voie ferree",
        "ligne a haute tension",
        "nuisances sonores",
        "bruyant",
        "passage frequent",
        "axe passant",
    ],
    MITOYEN: ["mitoyen", "mitoyennete", "accole"],
}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("vis a vis", "vis-a-vis")
    return re.sub(r"\s+", " ", text)


def classify_quality(*parts: str | None) -> dict:
    """Renvoie features (aménités), nuisances, nature_score et nature_exception."""
    text = _normalize(" ".join(p for p in parts if p))
    if not text:
        return {"features": [], "nuisances": [], "nature_score": 0, "nature_exception": False}

    features = {
        tag for tag, kws in _POSITIVE_KEYWORDS.items() if any(kw in text for kw in kws)
    }

    # « sans vis-à-vis » ne doit pas compter comme une nuisance « vis-à-vis ».
    neg_text = text
    for phrase in ("sans vis-a-vis", "aucun vis-a-vis", "sans aucun vis-a-vis"):
        neg_text = neg_text.replace(phrase, " ")
    nuisances = {
        tag for tag, kws in _NEGATIVE_KEYWORDS.items() if any(kw in neg_text for kw in kws)
    }

    nature_score = len(features) - len(nuisances)
    nature_exception = (
        bool(features & _STRONG)
        and len(features) >= 3
        and NUISANCES not in nuisances
        and VIS_A_VIS not in nuisances
    )
    return {
        "features": sorted(features),
        "nuisances": sorted(nuisances),
        "nature_score": nature_score,
        "nature_exception": nature_exception,
    }
