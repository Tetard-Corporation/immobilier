"""Espace modulable en chambre type dortoir : le volume qu'on peut convertir en couchages.

Ce que le critère cherche, et qu'aucun autre du set ne voit : une maison de retrait entre
copains se juge aussi sur le nombre de gens qu'elle couche un week-end de janvier. Les
chambres sont déjà comptées (`chambres_min`), mais elles disent la capacité EXISTANTE ;
ici on mesure la capacité qu'on peut se donner — une grange attenante, des combles
aménageables, une dépendance, un atelier, une mezzanine.

Pourquoi une mesure à part et pas un `feature` de plus :

- un `feature` est présent ou absent, et quand il est absent il vaut `n/a` — donc il ne
  peut que faire MONTER celui dont l'annonce a employé le mot (c'est le défaut relevé sur
  les cinq features bretonnes, écart-type nul et poids 5). Ici on note toujours, et le
  silence de l'annonce vaut le socle : une annonce énumère ses annexes, c'est de la
  surface et ça se vend ;
- tous les volumes ne se valent pas. Une grange de 120 m² au sol et « de beaux volumes »
  ne sont pas la même promesse : le second est du vocabulaire d'agence. D'où trois
  niveaux de signal, le plus faible ne pouvant pas porter la note à lui seul.

Mesuré sur les 6 455 annonces du set têtard : noté sur 100 % d'entre elles, moyenne 0,45,
écart-type 0,30. 48 % des biens restent au socle, 12 % prennent la note pleine. Corrélation
avec la surface bâtie : 0,04, et c'est le point — la grange et les combles ne comptent PAS
dans la surface habitable, donc ce critère ne rachète pas ce que `logement_compact`
pénalise et ne paie pas deux fois ce que `surface_habitable` note.
"""

from __future__ import annotations

import re
import unicodedata

# Poids d'un signal, par niveau de preuve.
_FORT = 0.55     # un volume identifié, dont l'annonce dit qu'il existe et qu'il est convertible
_MOYEN = 0.30    # un volume réel, mais dont rien ne dit qu'on peut y dormir
_FAIBLE = 0.12   # une tournure d'agence : ça n'emporte rien tout seul
# Somme des poids au-delà de laquelle la note est pleine. Calée pour qu'UN signal fort ne
# suffise pas (0,64) et que deux emportent la note : une grange citée en passant n'est pas
# un dortoir, une grange PLUS des combles aménageables, si.
_SATURATION = 1.0
# Note quand l'annonce a été lue et ne décrit aucun volume convertible. Non nulle : un
# grenier existe dans beaucoup de maisons anciennes sans que l'annonce le cite. Basse :
# le groupe demande un espace prouvé, pas un espace probable.
_SOCLE = 0.20

# (tag, poids, libellé lisible, motif). Ordre = ordre d'affichage du détail.
SIGNAUX: list[tuple[str, float, str, str]] = [
    ("grange", _FORT, "grange", r"\bgranges?\b"),
    ("dependance", _FORT, "dépendance", r"\bd[ée]pendances?\b"),
    ("combles_amenageables", _FORT, "combles aménageables",
     r"\bcombles?\b[^.]{0,40}\bam[ée]nage(?:able|ables|e|es|s)?\b|\bam[ée]nageables?\b[^.]{0,20}\bcombles?\b"),
    ("grenier_amenageable", _FORT, "grenier aménageable",
     r"\bgreniers?\b[^.]{0,40}\bam[ée]nage(?:able|ables|e|es|s)?\b"),
    ("sous_sol_amenageable", _FORT, "sous-sol aménageable",
     r"sous-?\s?sol[^.]{0,40}\bam[ée]nage(?:able|ables|e|es|s)?\b"),
    ("batiment_annexe", _FORT, "bâtiment annexe",
     r"\bhangars?\b|\bbergeries?\b|\b[ée]curies?\b|\b[ée]tables?\b|\bremises?\b|"
     r"\bchalet\s+d[' ]alpage\b|\bfenil\b|\bcorps\s+de\s+ferme\b"),
    # « gîte » n'est presque jamais le gîte du village : sur les 233 annonces du set qui
    # l'emploient, il désigne ce qu'on POURRAIT faire du volume (« pouvant être aménagé
    # en gîte »). C'est exactement le signal cherché.
    ("logement_independant", _FORT, "logement indépendant possible",
     r"studio\s+ind[ée]pendant|appartement\s+ind[ée]pendant|\bg[îi]tes?\b|"
     r"logement\s+ind[ée]pendant|deuxi[èe]me\s+logement"),
    ("piece_amenageable", _FORT, "pièce à aménager",
     r"(?:pi[èe]ces?|espaces?|volumes?|surfaces?)\s+(?:[àa]\s+)?am[ée]nager|"
     r"(?:pi[èe]ces?|espaces?|volumes?)\s+am[ée]nageables?"),
    ("dortoir", _FORT, "dortoir / couchages", r"\bdortoirs?\b|\bcouchages?\b"),
    ("mezzanine", _MOYEN, "mezzanine", r"\bmezzanines?\b"),
    ("atelier", _MOYEN, "atelier", r"\bateliers?\b"),
    ("salle_de_jeux", _MOYEN, "salle de jeux", r"salle\s+de\s+jeux?"),
    ("combles", _MOYEN, "combles", r"\bcombles?\b"),
    ("grenier", _MOYEN, "grenier", r"\bgreniers?\b"),
    ("modulable", _MOYEN, "espace modulable", r"\bmodulables?\b|\bloft\b"),
    ("grands_volumes", _FAIBLE, "beaux volumes",
     r"(?:beaux|grands|jolis|superbes)\s+volumes|volumes?\s+(?:g[ée]n[ée]reux|impressionnants)|"
     r"\bgrand\s+volume\b"),
    ("grande_piece", _FAIBLE, "grande pièce", r"grande\s+pi[èe]ce|vaste\s+pi[èe]ce|grande\s+salle"),
]

_POIDS = {tag: poids for tag, poids, _, _ in SIGNAUX}
_LIBELLES = {tag: lib for tag, _, lib, _ in SIGNAUX}
_MOTIFS = [(tag, re.compile(motif)) for tag, _, _, motif in SIGNAUX]

# « combles perdus » est le contraire d'un espace modulable, et l'annonce le dit dans les
# mêmes mots que « combles aménageables ». La négation ne retire que les signaux nus
# (combles, grenier) : elle ne peut pas effacer une grange citée trois phrases plus loin.
_NEGATION = re.compile(r"combles?\s+perdus|non\s+am[ée]nageable|pas\s+am[ée]nageable")
_NIABLES = ("combles", "grenier")

# « Combles aménageables » déclenche aussi « combles » : le signal fort ABSORBE le signal
# nu, sinon un même volume est payé deux fois (0,55 + 0,30) et des combles finissent par
# valoir plus qu'une grange. C'est le défaut que le §3 de docs/criteres.md reproche aux
# critères corrélés, en plus petit et à l'intérieur d'un seul barème.
_ABSORBE = {"combles_amenageables": "combles", "grenier_amenageable": "grenier"}

# La cave est volontairement absente : citée par 31 % des annonces du set, elle ne se
# transforme pas en chambre. Un signal que presque tout le monde a ne départage personne.


def _normalise(texte: str | None) -> str:
    t = unicodedata.normalize("NFKD", texte or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t.lower())


def detecter(*parties: str | None) -> list[str] | None:
    """Les volumes convertibles cités par l'annonce, dans l'ordre du registre.

    `None` quand il n'y a aucun texte à lire — et non la liste vide, qui veut dire
    « annonce lue, rien trouvé » et vaut le socle. Une annonce sans description ne prouve
    pas l'absence de grange : elle ne prouve rien, donc le critère sort du calcul plutôt
    que de pénaliser un bien pour un silence qui n'est pas le sien.
    """
    texte = _normalise(" ".join(p for p in parties if p))
    if not texte:
        return None
    trouves = [tag for tag, motif in _MOTIFS if motif.search(texte)]
    if _NEGATION.search(texte):
        trouves = [t for t in trouves if t not in _NIABLES]
    absorbes = {_ABSORBE[t] for t in trouves if t in _ABSORBE}
    return [t for t in trouves if t not in absorbes]


def noter(signaux: list[str] | None, params: dict | None = None) -> float:
    """Note [0,1] : socle, plus les signaux relevés, saturée avant l'inventaire complet."""
    params = params or {}
    socle = params.get("socle", _SOCLE)
    saturation = params.get("saturation", _SATURATION) or _SATURATION
    poids = sum(_POIDS.get(s, 0.0) for s in (signaux or []))
    return round(min(1.0, socle + (1 - socle) * min(1.0, poids / saturation)), 3)


def resumer(signaux: list[str] | None) -> str:
    """Ce que l'annonce a dit, en clair — pour la ligne de détail du bien."""
    if not signaux:
        return "aucun volume convertible décrit dans l'annonce"
    return " · ".join(_LIBELLES.get(s, s) for s in signaux)
