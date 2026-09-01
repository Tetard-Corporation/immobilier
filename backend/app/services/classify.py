"""Classification de l'état d'un bien à partir du texte des annonces.

Une ruine et un bien « à rénover » n'impliquent pas le même volume de travaux :
on utilise une échelle ordinale (du plus léger au plus lourd) et on retient le
niveau le plus sévère mentionné, en neutralisant d'abord les négations
(« aucun travaux », « sans travaux »...).

Deux niveaux de lecture, et le second existe parce que le premier a laissé passer
une ruine en tête de classement (Jarrier, 73, notée 89,4) :

- les MOTS-CLÉS, balayés du plus sévère au moins sévère ;
- les RÈGLES, qui lisent la nature du bien mis en vente. « Grange à rénover » n'est
  pas un chantier de second œuvre : c'est une construction complète — planchers,
  isolation, réseaux, ouvertures — dans une enveloppe existante. Le mot-clé
  « à rénover » la rangeait avec la maison qui a besoin d'une cuisine.
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
            "hors d eau hors d air a refaire",
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
            # Le verbe seul, comme « renover » plus bas : « un projet établi par un
            # cabinet d'architecte afin de LA réhabiliter en une seule habitation ». Avec
            # les frontières de mots, « a rehabiliter » ne matche plus dans « la
            # réhabiliter », et le bien repassait en état inconnu.
            "rehabiliter",
            "a restaurer entierement",
            "tout a refaire",
            "travaux importants",
            # « À rénover ENTIÈREMENT » : le mot-clé « a renover » seul rangeait la
            # formule avec le rafraîchissement (0,85, donc admissible), alors qu'elle
            # dit exactement ce que le groupe refuse. Vécu sur la grange de Jarrier
            # (73), 190 m², 142 k€, notée 89,4 et publiée comme pépite.
            "a renover entierement",
            "a renover integralement",
            "a renover de fond en comble",
            "entierement a renover",
            "renover entierement",
            "renovation integrale",
            "renovation de fond en comble",
            "de fond en comble",
            "a reprendre entierement",
            "a amenager entierement",
            "tout est a faire",
            # Enveloppe close, intérieur vide : le second œuvre reste ENTIER.
            "hors d eau hors d air",
            # L'annonce dit elle-même que le bien n'est pas un logement.
            "n est pas habitable",
            "pas habitable en l etat",
            "non habitable en l etat",
            "inhabitable en l etat",
            "impropre a l habitation",
            "non habitable en etat",
        ],
    ),
    (
        RENOVER,
        [
            "a renover",
            # Le verbe seul : « pour qui souhaite rénover », « il reste à rénover la
            # cave ». Sans lui, une annonce qui parle de rénovation sans écrire « à
            # rénover » tombait au niveau suivant — et « renove » y matchait à l'intérieur
            # de « renover », d'où un verdict *habitable*.
            "renover",
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
    "rien a renover",
    "plus rien a renover",
]


# --- Règle « coquille » : ce qui est mis en vente n'est pas un logement ----------------
#
# Une grange, un hangar, une étable « à rénover » ou « à aménager » ne demandent pas le
# chantier d'une maison à rénover : il faut créer les planchers, l'isolation, les
# réseaux, les ouvertures, souvent la charpente. Les mots-clés ne voyaient que
# « à rénover » et rangeaient le bâtiment agricole avec la maison qui a besoin d'une
# cuisine — la grange de Jarrier (73) est ainsi montée à 89,4 et a été publiée comme
# pépite alors que le set exige « pas de rénovation complète ».
#
# Trois garde-fous, tous mesurés sur le lot (218 bascules, dont 4 fausses au premier
# jet) :
#
# 1. La coquille doit être CE QU'ON VEND. « Maison de village avec grange attenante »
#    parle d'une maison : le mot « maison » y vient avant « grange », et c'est ce que
#    teste `_position`.
# 2. Il faut une intention de conversion EXPLICITE (« à rénover », « à aménager »).
#    « Potentiel », « projet » et « travaux » ont d'abord été acceptés : un corps de
#    ferme habitable vanté pour son « potentiel d'aménagement moderne » basculait alors
#    en gros travaux.
# 3. Frontières de mots. Sans elles, « ancien moulinage à rafraîchir » matchait
#    « ancien moulin ».
_COQUILLES = (
    "grange", "hangar", "etable", "ecurie", "bergerie", "sechoir", "fenil", "mazot",
    "batiment agricole", "corps de ferme", "ancienne ferme", "chalet d alpage",
)
# « moulin » a été retiré : `iad France - Audrey Moulin vous propose: Charmante Maison
# de Campagne à rénover` faisait passer une maison pour une coquille, le patronyme de
# l'agent arrivant avant le mot « maison ». Le préambule d'agence est désormais coupé
# (`_sans_preambule`), mais un nom de famille reste un nom de famille : un moulin se
# vend trop rarement pour valoir ce risque.
# Mots qui désignent un logement : s'ils viennent AVANT la coquille, c'est un logement
# qui a une grange, pas une grange qu'on vend.
_LOGEMENTS = ("maison", "villa", "appartement", "pavillon", "longere", "mas",
              "propriete", "demeure", "habitation", "logement", "chalet")
# La coquille est déjà un logement : la règle ne doit pas se déclencher. Bornes de mot
# pour que « renove » n'attrape pas « renover » (le piège inverse de celui qu'on corrige).
_DEJA_CONVERTI = re.compile(
    r"\b(?:renove(?:e|s|es)?|rehabilite(?:e|s|es)?|restauree?s?|amenagee?s?|"
    r"transformee?s?|convertie?s?|habitable de suite|habitable immediatement)\b")
# Ce qui reste à faire, et seulement dit explicitement.
_A_CONVERTIR = re.compile(
    r"\ba (?:renover|amenager|restaurer|rehabiliter|transformer|convertir|finir|terminer)\b"
    r"|\bamenageable\b|\ba usage d habitation a creer\b")


def _mots(text: str, mots) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(re.escape(m) for m in mots) + r")\b")


_RE_COQUILLES = _mots("", _COQUILLES)
_RE_LOGEMENTS = _mots("", _LOGEMENTS)


def _position(text: str, motif: re.Pattern) -> int | None:
    """Index de la première occurrence, None si aucune."""
    m = motif.search(text)
    return m.start() if m else None


# Préambule d'agence : « iad France - Céline Chapuis vous propose: ... ». Il précède
# le bien et fausse le test de position (le premier mot du texte n'est plus la nature
# de ce qu'on vend mais le nom du mandataire).
_PREAMBULE = re.compile(r"^.{0,110}?vous (?:propose|presente)\s*:?\s*")


def _sans_preambule(text: str) -> str:
    m = _PREAMBULE.match(text)
    if not m:
        return text
    # On ne coupe que si le préambule ne dit RIEN du bien. « Maison 4 pièces 90 m² —
    # Queige, à quelques kilomètres d'Albertville [...] je vous propose » commence par
    # la nature du bien : la couper faisait passer une maison pour une grange.
    tete = m.group(0)
    if _RE_LOGEMENTS.search(tete) or _RE_COQUILLES.search(tete):
        return text
    return text[m.end():]


# Distance maximale entre la coquille et ce qu'on veut en faire. « Grange à rénover »
# les colle ; « ancienne ferme édifiée en 1871 [...] 164 m² habitables [...] les
# dépendances représentent un potentiel d'aménagement » les sépare de 500 signes et
# décrit une maison habitable dont les annexes sont à reprendre. Sans cette fenêtre, la
# règle lisait la première et la dernière phrase d'une annonce comme une seule.
_FENETRE = 80


def regle_coquille(text: str) -> bool:
    """Le bien mis en vente est-il une coquille à convertir (grange, hangar, étable) ?"""
    text = _sans_preambule(text)
    if _DEJA_CONVERTI.search(text):
        return False
    pos_logement = _position(text, _RE_LOGEMENTS)
    for m in _RE_COQUILLES.finditer(text):
        # La coquille doit être ce qu'on vend, pas une annexe citée après le logement.
        if pos_logement is not None and m.start() > pos_logement:
            break
        fenetre = text[max(0, m.start() - _FENETRE):m.end() + _FENETRE]
        if _A_CONVERTIR.search(fenetre):
            return True
    return False


# Les mots-clés se cherchent avec des FRONTIÈRES DE MOTS, pas en sous-chaîne.
# « souhaite rénover » contient « renove » : une maison de 1920 dont l'annonce dit
# « offre un beau potentiel pour qui souhaite rénover » a ainsi été déclarée *habitable*
# (Ugine, 180 000 €, notée 83,7 et publiée comme pépite). Le mot-clé de rafraîchissement
# ne pouvait pas la rattraper — il vient d'un niveau moins sévère et le balayage s'arrête
# au premier niveau qui matche.
_MOTIFS = [(condition, re.compile(r"\b(?:" + "|".join(re.escape(k) for k in kws) + r")\b"))
           for condition, kws in _KEYWORDS]


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    # Les apostrophes deviennent des espaces : « n'est pas habitable » et « n est pas
    # habitable » doivent se lire pareil, et les annonces mélangent ' et ’ (le second
    # survit à la décomposition NFKD, le premier non — deux textes indiscernables à
    # l'œil ne matchaient donc pas les mêmes mots-clés).
    text = re.sub(r"[\u2018\u2019\u02bc'`\u00b4]", " ", text)
    return re.sub(r"\s+", " ", text)


# Fin de texte coupée : les portails tronquent leurs cartes de liste (SeLoger s'arrête à
# ~200 signes) et signalent la coupe par une ellipse.
_TRONQUE_RE = re.compile(r"(?:\.{3}|…)[\s\"'»)\]]*$")


def est_tronque(*parts: str | None) -> bool:
    return any(p and _TRONQUE_RE.search(p.rstrip()) for p in parts)


def classify(*parts: str | None) -> dict:
    """Analyse titre/description et renvoie {'condition': str|None, 'niveau_travaux': int|None}.

    `condition` vaut None si aucun indice n'est trouvé (état inconnu).
    """
    brut = [p for p in parts if p]
    text = _normalize(" ".join(brut))
    if not text:
        return {"condition": None, "niveau_travaux": None}

    # Neutralise les négations : on remplace par un marqueur "habitable" explicite
    # pour éviter qu'un « aucun travaux à prévoir » ne déclenche « à rénover ».
    negated = False
    for neg in _NEGATIONS:
        if neg in text:
            negated = True
            text = text.replace(neg, " ")

    trouve = None
    for condition, motif in _MOTIFS:
        if motif.search(text):
            trouve = condition
            break

    # La règle « coquille » s'ajoute aux mots-clés au lieu de les remplacer, et on garde
    # le niveau LE PLUS SÉVÈRE des deux : une grange décrite comme ruine reste une ruine,
    # une grange « à rénover » cesse d'être un simple chantier de rénovation.
    if regle_coquille(text):
        if trouve is None or NIVEAU[trouve] < NIVEAU[GROS_TRAVAUX]:
            trouve = GROS_TRAVAUX

    if trouve is None and negated:
        trouve = HABITABLE

    # Un texte COUPÉ ne donne qu'une BORNE INFÉRIEURE : la troncature ne peut pas
    # inventer une mention de travaux, seulement en cacher une. Un verdict léger rendu
    # sur un texte coupé ne prouve donc rien — c'est ainsi qu'une maison de 1920 dont
    # l'annonce s'arrête sur « souhaite rénover e… » a été déclarée habitable (Ugine,
    # 180 000 €, publiée comme pépite).
    #
    # On ne garde donc, sur un texte tronqué, que les verdicts SÉVÈRES : eux sont déjà au
    # bout de l'échelle, la suite du texte ne peut pas les aggraver. Les autres repassent
    # à « état inconnu », et le set applique alors sa règle — un bien dont on ignore
    # l'état ne se juge pas, exactement comme un bien sans photo.
    #
    # 1 111 des 1 140 descriptions tronquées viennent des cartes de la SERP SeLoger, qui
    # s'arrêtent à ~200 signes. La vraie réparation est d'aller lire la fiche du bien ;
    # en attendant, mieux vaut ignorer l'état que le sous-estimer.
    if trouve is not None and NIVEAU[trouve] < NIVEAU[GROS_TRAVAUX] and est_tronque(*brut):
        return {"condition": None, "niveau_travaux": None}

    if trouve is not None:
        return {"condition": trouve, "niveau_travaux": NIVEAU[trouve]}
    return {"condition": None, "niveau_travaux": None}
