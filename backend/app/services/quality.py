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
VUE = "vue"  # vue sur un élément (mer, montagne, vallée...)
VUE_PANORAMIQUE = "vue_panoramique"  # vue dégagée / panoramique / imprenable / 360°
FORET = "foret"
EAU = "eau"
CALME = "calme"
ISOLE = "isole"
SANS_VIS_A_VIS = "sans_vis_a_vis"
ARBORE = "arbore"
ENSOLEILLE = "ensoleille"
AUTHENTIQUE = "authentique"

FEATURES = [
    VUE, VUE_PANORAMIQUE, FORET, EAU, CALME, ISOLE, SANS_VIS_A_VIS, ARBORE, ENSOLEILLE, AUTHENTIQUE
]
_STRONG = {VUE, VUE_PANORAMIQUE, FORET, EAU, ISOLE}

# Nuisances négatives.
VIS_A_VIS = "vis_a_vis"
NUISANCES = "nuisances"
MITOYEN = "mitoyen"

_POSITIVE_KEYWORDS: dict[str, list[str]] = {
    # Vue DÉGAGÉE / PANORAMIQUE : un champ de vision large, sans obstacle (critère "Léo").
    VUE_PANORAMIQUE: [
        "vue panoramique",
        "panorama",
        "vue a 360",
        "vue a 180",
        "vue degagee",
        "vue tres degagee",
        "vue imprenable",
        "vue sans vis-a-vis",
        "vue sans aucun obstacle",
        "vue plongeante",
        "vue dominante",
        "position dominante",
        "domine la vallee",
        "surplombant",
        "surplombe",
        "en surplomb",
        "a flanc de coteau",
        "a flanc de montagne",
        "balcon sur la vallee",
        "belvedere",
        "plein ciel",
        "vue grand large",
        "vue a perte de vue",
        "vue lointaine",
        "horizon degage",
        "vue exceptionnelle",
        "vue spectaculaire",
        "vue epoustouflante",
        "vue grandiose",
    ],
    # Vue sur un élément précis (sans nécessairement être panoramique).
    VUE: [
        "vue mer",
        "vue sur mer",
        "vue sur la mer",
        "vue montagne",
        "vue sur la montagne",
        "vue sur les montagnes",
        "vue sur la vallee",
        "vue sur le massif",
        "vue sur les sommets",
        "vue sur le lac",
        "vue sur la campagne",
        "vue sur les vignes",
        "belle vue",
        "tres belle vue",
        "magnifique vue",
        "superbe vue",
        "jolie vue",
        "point de vue",
        "avec vue",
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
    AUTHENTIQUE: [
        "authentique",
        "de caractere",
        "plein de charme",
        "beaucoup de charme",
        "beaucoup de cachet",
        "du cachet",
        "plein de cachet",
        "avec cachet",
        "cachet",
        "caractere",
        "charme de l'ancien",
        "pierres apparentes",
        "poutres apparentes",
        "en pierre",
        "vieilles pierres",
        "belles pierres",
        "pierres de taille",
        "pierre de pays",
        "tomettes",
        "tommettes",
        "four a pain",
        "cheminee",
        "colombages",
        "batisse de caractere",
        "batisse",
        "corps de ferme",
        "ancienne ferme",
        "ferme renovee",
        "longere",
        "grange attenante",
        "maison de maitre",
        "demeure de caractere",
        "mas en pierre",
        "vieux mas",
    ],
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
    # Une vue panoramique est aussi une vue.
    if VUE_PANORAMIQUE in features:
        features.add(VUE)

    # « sans vis-à-vis » ne doit pas compter comme une nuisance « vis-à-vis ».
    neg_text = text
    for phrase in ("sans vis-a-vis", "aucun vis-a-vis", "sans aucun vis-a-vis"):
        neg_text = neg_text.replace(phrase, " ")
    nuisances = {
        tag for tag, kws in _NEGATIVE_KEYWORDS.items() if any(kw in neg_text for kw in kws)
    }

    nature_score = len(features) - len(nuisances)
    # « authentique » = cachet du bâti, pas le cadre naturel : exclu du caractère d'exception.
    nature_feats = features - {AUTHENTIQUE}
    nature_exception = (
        bool(nature_feats & _STRONG)
        and len(nature_feats) >= 3
        and NUISANCES not in nuisances
        and VIS_A_VIS not in nuisances
    )
    return {
        "features": sorted(features),
        "nuisances": sorted(nuisances),
        "nature_score": nature_score,
        "nature_exception": nature_exception,
    }
