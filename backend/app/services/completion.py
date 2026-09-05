"""Complétion des champs structurels manquants à partir du TEXTE de l'annonce.

Le catalogue est alimenté par des sources qui ne donnent pas les mêmes champs. Bien'ici
publie `bedroomsQuantity`, SeLoger l'écrit dans sa ligne de faits, Leboncoin le range
dans ses attributs — mais aucun ne le fait toujours, et une annonce d'agence n'a que du
texte. Mesuré sur le jeu publié du 1er septembre : **117 biens sur 172 sans nombre de
chambres, 74 sans surface de terrain, 63 sans état du bâti**, alors que 89 de ces textes
écrivent le compte des chambres en toutes lettres. Le site affichait « ? ch · terrain — »
sur des biens dont l'annonce dit « À l'étage, quatre chambres [...] terrain clos de
788 m² ».

Ce module lit ce que l'annonce dit et remplit les trous. Il ne CORRIGE jamais une valeur
donnée par la source : une donnée structurée du portail vaut mieux qu'une phrase de
commercial.

Trois règles, chacune payée par une erreur déjà vue en base :

1. **Une valeur fausse est pire qu'une valeur absente.** Chaque motif est refusé dès
   qu'un marqueur d'irréel le précède — « possibilité d'une 4ème chambre », « potentiel
   de 200 m² habitables après rénovation », « pour porter la superficie du terrain à
   3 765 m² » décrivent ce que le bien n'est pas.
2. **Les chambres se comptent PAR NIVEAU.** Une annonce énumère le logement étage par
   étage (« au rez-de-chaussée, trois chambres [...] à l'étage, une chambre ») ou le
   résume (« comprend 3 chambres ») puis le détaille. Sommer tous les comptes donne 6 là
   où il y en a 3 ; prendre le maximum donne 3 là où il y en a 5. On somme donc les
   niveaux entre eux, en gardant le maximum À L'INTÉRIEUR de chaque niveau, et le résumé
   (ce qui est dit avant tout marqueur d'étage) fait plancher plutôt que de s'ajouter.
3. **Une surface habitable se lit d'abord, pas en dernier.** Le premier motif qui matche
   gagne, dans l'ordre de fiabilité (« 120 m² habitables » avant « maison de 120 m² ») —
   prendre le plus grand nombre de la page ramenait la surface du terrain (« terrain de
   450 m² | maison de 83 m² » lu comme 450 m² de bâti).

Justesse mesurée contre les biens dont la source DONNE la valeur (7 086 biens en base) :

| champ             | renseigné par le texte | exact | sous-estimé | surestimé |
|-------------------|------------------------|-------|-------------|-----------|
| `nb_chambres`     | 2 506 / 3 718          |  73 % |     23 %    |    4 %    |
| `nb_pieces`       | 3 772 / 6 446          |  87 % |     12 %    |    1 %    |
| `surface_terrain` | 2 850 / 5 369 (±5 %)   |  89 % |      8 %    |    3 %    |
| `surface_bati`    | 5 025 / 6 666 (±5 %)   |  91 % |      6 %    |    3 %    |

Le repère qui compte pour les chambres n'est pas 100 % mais **le repli qu'on remplace** :
`preferences.chambres_min` estime aujourd'hui « pièces - 1 » quand les chambres manquent.
Ce repli est exact 46 % du temps et **surestime 51 % des biens** — la direction
dangereuse, celle qui a fait entrer un mobil-home dans les pépites avec « 3 chambres
estimées ». Lire le texte est exact 73 % du temps et ne surestime que 4 %.

Voir `tests/test_completion.py` et `scripts/completer.py` (application aux biens en base).
"""

from __future__ import annotations

import re
import unicodedata

# --- Normalisation --------------------------------------------------------------------

_TAGS = re.compile(r"<[^>]+>")
# Les séparateurs de milliers vus en vrai (cf. HeuristicExtractor) : espace insécable et
# fine insécable. Oublier la fine fait lire « 1 200 m² » comme 1 m².
_ESPACES_FINES = "   "


def normalize(*parts: str | None) -> str:
    """Texte d'annonce ramené à une forme comparable : sans HTML, sans accents."""
    text = " ".join(p for p in parts if p)
    text = _TAGS.sub(" ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    # Apostrophes typographiques et droites lues pareil (cf. classify._normalize).
    text = re.sub(r"[‘’ʼ'`´]", " ", text)
    for c in _ESPACES_FINES:
        text = text.replace(c, " ")
    return re.sub(r"\s+", " ", text)


# --- Nombres --------------------------------------------------------------------------

_MOTS = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
         "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10}
_MOT_RE = "|".join(_MOTS)
_ORDINAUX = {"premiere": 1, "deuxieme": 2, "seconde": 2, "troisieme": 3, "quatrieme": 4,
             "cinquieme": 5, "sixieme": 6, "septieme": 7, "huitieme": 8}


def _compte(brut: str | None) -> int | None:
    if brut is None:
        return None
    brut = brut.strip()
    if brut in _MOTS:
        return _MOTS[brut]
    try:
        return int(brut)
    except ValueError:
        return None


def _surface(brut: str) -> float | None:
    """« 1 200 », « 788 », « 85,73 », « 1.779 » -> float."""
    cleaned = brut.strip().replace(" ", "")
    # Un point sépare les milliers dans « 1.779 m² » mais décime dans « 85.73 m² » :
    # trois chiffres derrière = milliers, sinon décimale.
    cleaned = re.sub(r"\.(\d{3})\b", r"\1", cleaned)
    cleaned = cleaned.replace(",", ".").rstrip(".")
    try:
        return float(cleaned)
    except ValueError:
        return None


# Fenêtre de lecture en amont d'un compte, pour y chercher un marqueur d'irréel.
# 45 signes : assez pour « possibilité d'une 4ème », trop court pour attraper la phrase
# d'avant et refuser un compte qui n'a rien à voir avec elle.
_AVANT = 45
# La fenêtre s'arrête AUSSI à la ponctuation forte. Sans ça, « [...] terrain clos de
# 788 m². 120 m² habitables » refusait les 120 m² : le mot « terrain » de la phrase
# précédente tombait dans les 45 signes.
_FIN_DE_PHRASE = re.compile(r"[.;!?|]")


def _amont(texte: str, pos: int, taille: int = _AVANT) -> str:
    """Ce qui précède `pos` dans la MÊME phrase, au plus `taille` signes."""
    debut = texte[max(0, pos - taille):pos]
    coupe = None
    for m in _FIN_DE_PHRASE.finditer(debut):
        coupe = m.end()
    return debut[coupe:] if coupe is not None else debut

# Le mode irréel : l'annonce décrit ce que le bien POURRAIT être. Ce qu'elle vend est
# ailleurs dans le texte, ou nulle part.
_IRREEL = (r"possibilit|possible|pourrait|permettrait|creer|amenager|amenageable|"
           r"transformer|convertir|potentiel|projet|envisager|imaginer|ajouter|gagner|"
           r"pour porter|apres renovation|a terminer|a finir")


# --- Chambres -------------------------------------------------------------------------

# Adjectifs qui s'intercalent entre le compte et le mot : « 3 grandes chambres », « quatre
# belles chambres lumineuses ». Sans eux, les annonces rédigées (par opposition aux fiches
# techniques) ne matchent rien.
_ADJ = (r"(?:(?:tres|tout aussi)\s+)?(?:grande|belle|jolie|vaste|spacieuse|petite|autre|"
        r"magnifique|superbe|lumineuse|agreable|coquette|confortable|bonne|nouvelle|"
        r"immense|charmante|vraie|veritable|derniere|autre)s?\s+")

_CHAMBRES = re.compile(rf"\b(\d{{1,2}}|{_MOT_RE})\s+(?:{_ADJ}){{0,3}}chambres?\b")
# Écriture de fiche technique : « chambres : 3 », « chambre(s) : 2 ». Le nombre ne doit
# PAS être suivi d'une unité de surface : les annonces qui détaillent pièce par pièce
# écrivent « chambre : 12 m2 », et une maison de 93 m² est alors entrée à 12 chambres.
_CHAMBRES_FICHE = re.compile(
    r"\bchambres?\s*\(?s?\)?\s*[:=]\s*(\d{1,2})\b(?!\s*m\s*(?:2|²))")
# Un ORDINAL est un compte minimum : « une troisième chambre indépendante » prouve trois
# chambres, même quand l'énumération qui précède n'en nomme qu'une à la fois.
_CHAMBRE_ORDINALE = re.compile(
    rf"\b(?:({'|'.join(_ORDINAUX)})|(\d{{1,2}})\s*(?:eme|ere|er|e)\b)\s+"
    rf"(?:{_ADJ}){{0,2}}chambres?\b")

_PAS_UNE_CHAMBRE = re.compile(
    rf"(?:{_IRREEL}|de plus|supplementaire|chambre d hote|chambres d hote|airbnb)")
# Ce qui suit et disqualifie : « 2 chambres d'hôtes », « chambre froide ».
_SUFFIXE_HORS_SUJET = re.compile(r"^\s*(?:d hote|froide|de bonne|de service|funeraire|noire)")

# Marqueurs de NIVEAU : ils découpent l'énumération du logement. Les dépendances
# (« annexe », « studio indépendant ») en font partie — leurs chambres comptent aussi,
# et elles sont énumérées à part.
_NIVEAU = re.compile(
    r"\b(?:rez\s*-?\s*de\s*-?\s*chaussee|rdc|au rez|a l etage|l etage|"
    r"(?:1er|1 er|premier|2e|2eme|deuxieme|second|3e|3eme|troisieme|4e|4eme|quatrieme|"
    r"dernier)\s+(?:et\s+)?etage|etage superieur|sous\s*-?\s*combles|les combles|"
    r"sous\s*-?\s*sol|niveau \d|mezzanine|palier|rez\s*-?\s*de\s*-?\s*jardin|"
    r"niveau inferieur|niveau superieur|espace nuit|partie nuit|zone nuit|"
    r"etage sous combles|dernier niveau|premier niveau|deuxieme niveau|annexe|"
    r"dependance|studio independant|appartement independant)\b")

# Plafond de vraisemblance : au-delà on lit un hôtel ou une faute de saisie, pas un bien
# du catalogue.
_MAX_CHAMBRES = 12


def _comptes_chambres(texte: str) -> list[tuple[int, int]]:
    """[(position, compte)] des mentions de chambres retenues."""
    trouves: list[tuple[int, int]] = []
    for m in _CHAMBRES.finditer(texte):
        if _PAS_UNE_CHAMBRE.search(_amont(texte, m.start())):
            continue
        if _SUFFIXE_HORS_SUJET.match(texte[m.end():m.end() + 20]):
            continue
        n = _compte(m.group(1))
        if n is not None and 1 <= n <= _MAX_CHAMBRES:
            trouves.append((m.start(), n))
    for m in _CHAMBRES_FICHE.finditer(texte):
        n = _compte(m.group(1))
        if n is not None and 1 <= n <= _MAX_CHAMBRES:
            trouves.append((m.start(), n))
    return sorted(trouves)


def chambres(texte: str) -> int | None:
    """Nombre de chambres annoncé, ou None si le texte ne le dit pas.

    Somme les NIVEAUX entre eux, maximum à l'intérieur d'un niveau ; le résumé (avant
    tout marqueur d'étage) et le plus grand ordinal rencontré font plancher.
    """
    trouves = _comptes_chambres(texte)
    ordinaux = []
    for m in _CHAMBRE_ORDINALE.finditer(texte):
        if _PAS_UNE_CHAMBRE.search(_amont(texte, m.start())):
            continue
        n = _ORDINAUX.get(m.group(1) or "") or _compte(m.group(2))
        if n is not None and 1 <= n <= _MAX_CHAMBRES:
            ordinaux.append(n)
    if not trouves and not ordinaux:
        return None

    niveaux = [m.start() for m in _NIVEAU.finditer(texte)]
    par_niveau: dict[int, list[int]] = {}
    for pos, n in trouves:
        precedents = [i for i, debut in enumerate(niveaux) if debut < pos]
        par_niveau.setdefault(precedents[-1] if precedents else -1, []).append(n)

    resume = max(par_niveau.get(-1, [0]))
    enumere = sum(max(v) for k, v in par_niveau.items() if k >= 0)
    total = max(resume, enumere, max(ordinaux, default=0))
    return total or None


# --- Pièces ---------------------------------------------------------------------------

_PIECES = re.compile(rf"\b(\d{{1,2}}|{_MOT_RE})\s+(?:{_ADJ}){{0,2}}pieces?\b")
_PIECES_FICHE = re.compile(
    r"\bpieces?\s*\(?s?\)?\s*[:=]\s*(\d{1,2})\b(?!\s*m\s*(?:2|²))")
# Nomenclature française : T3 / F3 / type 3 = 3 pièces.
# « T3 », « T-3 », « F 3 », « type 3 » — et jusqu'à deux chiffres : les titres Orpi
# annoncent « T-17 » sur un immeuble, et le motif à un seul chiffre les laissait passer.
# Le tiret compte comme séparateur : c'est la forme qu'emploient Orpi et Century 21.
_TYPE_TF = re.compile(r"\b[tf]\s?-?\s?(\d{1,2})\b|\btype\s+(\d{1,2})\b")
# Forme des URL de recherche, gardée telle quelle comme texte par certaines agences :
# « /vente-maisons-4pieces-26410--893.php ». Le compte y est collé au mot.
_PIECES_SLUG = re.compile(r"(\d{1,2})\s?-?\s?pieces?\b")
_PAS_UNE_PIECE = re.compile(_IRREEL)
# « une pièce de vie de 36 m² » n'annonce pas un logement d'une pièce : le
# disqualifiant vient APRÈS le compte, là où le garde d'irréel ne regarde pas.
_PIECE_QUALIFIEE = re.compile(r"^\s*(?:de vie|a vivre|d eau|de bain|d hiver|de jour|"
                              r"de nuit|principale|supplementaire|en plus)")
_MAX_PIECES = 20


def pieces(texte: str) -> int | None:
    """Nombre de pièces annoncé, ou None.

    Le PREMIER compte l'emporte : le total est annoncé en tête (« maison 4 pièces
    120 m² »), le détail vient ensuite et ne parle que d'un niveau.
    """
    for m in _PIECES.finditer(texte):
        if _PAS_UNE_PIECE.search(_amont(texte, m.start())):
            continue
        if _PIECE_QUALIFIEE.match(texte[m.end():m.end() + 20]):
            continue
        n = _compte(m.group(1))
        if n is not None and 1 <= n <= _MAX_PIECES:
            return n
    for m in _PIECES_FICHE.finditer(texte):
        n = _compte(m.group(1))
        if n is not None and 1 <= n <= _MAX_PIECES:
            return n
    for m in _TYPE_TF.finditer(texte):
        n = _compte(m.group(1) or m.group(2))
        if n is not None and 1 <= n <= _MAX_PIECES:
            return n
    for m in _PIECES_SLUG.finditer(texte):
        n = _compte(m.group(1))
        if n is not None and 1 <= n <= _MAX_PIECES:
            return n
    return None


# --- Surfaces -------------------------------------------------------------------------

# Un nombre de surface : chiffres, espaces de groupement (« 1 200 »), et AU PLUS une
# partie décimale ou un groupe de milliers pointé (« 85,73 », « 1.779 »). Laisser le
# point et l'espace se répéter librement faisait traverser la ponctuation : « 788 m².
# 120 m² habitables » se lisait comme le nombre « 2. 120 ».
_NB = r"(\d[\d ]*(?:[.,]\d{1,3})?)"
_ENV = r"(?:d\s+|de\s+|:\s*)?(?:environ\s+|env\.?\s+|approx\.?\s+|pres de\s+|plus de\s+)?"
_M2 = r"\s*m\s*(?:2|²)"

# « terrain de 1 200 m² », « terrain clos et arboré d'environ 788 m2 », « parcelle : 900 m² »
_TERRAIN_AVANT = re.compile(
    rf"\b(?:terrain|parcelle|jardin|foncier)\b"
    rf"(?:\s+(?:cadastral|cadastrale|clos|close|arbore|arboree|plat|plate|attenant|"
    rf"attenante|constructible|viabilise|viabilisee|prive|privee|privatif|privative|"
    rf"paysager|paysage|paysagee|expose|exposee|sud|nord|est|ouest|de|d|et|au|aux|en|"
    rf"avec|pleine|propriete|non|clot|cloture|cloturee|magnifique|beau|belle|grand|"
    rf"grande|joli|jolie|vaste|petit|petite|agreable|loisirs|piscinable|total|totale)"
    rf"){{0,6}}\s*{_ENV}{_NB}{_M2}")
# « sur 1 779 m² de terrain », « 788 m2 de jardin »
_TERRAIN_APRES = re.compile(rf"{_NB}{_M2}\s+de\s+(?:terrain|parcelle|jardin)")
# « sur 1 ha », « 1,8 ha de terrain », « 19ha76a83 » (notation cadastrale ha/a/ca)
_HECTARES = re.compile(rf"{_NB}\s*(?:ha\b|hectares?\b)")

# Bornes de vraisemblance. Sous 20 m², le motif a lu une terrasse (« jardin d'hiver de
# 12 m² ») ; au-delà de 50 ha, une exploitation agricole ou une faute de saisie.
_TERRAIN_MIN, _TERRAIN_MAX = 20.0, 500_000.0

# L'annonce dit qu'il n'y a PAS de terrain. C'est une donnée, pas une absence de donnée :
# le critère `jardin` du set traite aujourd'hui « sans jardin » et « on ne sait pas »
# de la même façon, faute de pouvoir les distinguer.
_SANS_TERRAIN = re.compile(
    r"\b(?:pas de (?:jardin|terrain|exterieur)|sans (?:jardin|terrain|exterieur)|"
    r"aucun (?:jardin|terrain|exterieur)|ni jardin ni terrain)\b")


def terrain(texte: str) -> float | None:
    """Surface de terrain en m², 0.0 si l'annonce dit qu'il n'y en a pas, ou None.

    Le MAXIMUM : une annonce cite volontiers le jardin (petit) puis le terrain (grand),
    et c'est le terrain qui est la surface du bien.
    """
    surfaces: list[float] = []
    for motif in (_TERRAIN_AVANT, _TERRAIN_APRES):
        for m in motif.finditer(texte):
            if _PAS_UN_TERRAIN.search(_amont(texte, m.start())):
                continue
            s = _surface(m.group(1))
            if s is not None and _TERRAIN_MIN <= s <= _TERRAIN_MAX:
                surfaces.append(s)
    for m in _HECTARES.finditer(texte):
        if _PAS_UN_TERRAIN.search(_amont(texte, m.start())):
            continue
        s = _surface(m.group(1))
        if s is not None and 0 < s <= 50:
            surfaces.append(s * 10_000)
    if surfaces:
        return max(surfaces)
    if _SANS_TERRAIN.search(texte):
        return 0.0
    return None


_PAS_UN_TERRAIN = re.compile(_IRREEL)

# Surface habitable, du motif le plus fiable au moins fiable. Le premier qui donne une
# valeur gagne (règle 3).
_BATI_HABITABLE = re.compile(rf"{_NB}{_M2}\s*(?:habitables?|hab\b)")
_BATI_SURFACE = re.compile(
    rf"\bsurface(?:\s+(?:habitable|au sol|de plancher|utile))?\s*"
    rf"(?:[:=]|de|d\s+)?\s*{_ENV}{_NB}{_M2}")
_BATI_BIEN = re.compile(
    rf"\b(?:maison|appartement|villa|chalet|longere|mas|ferme|batisse|immeuble|"
    rf"propriete|demeure|studio|duplex|logement|habitation|grange|corps de ferme)"
    rf"\b[^.;]{{0,60}}?{_ENV}{_NB}{_M2}")
# Ce qui n'est pas de la surface habitable, cherché en amont du nombre.
_PAS_DU_BATI = re.compile(
    rf"(?:{_IRREEL}|terrain|parcelle|jardin|plancher|garage|cave|grenier|dependance|"
    rf"terrasse|piscine|combles)")
_BATI_MIN, _BATI_MAX = 9.0, 2_000.0


def bati(texte: str) -> float | None:
    """Surface habitable annoncée en m², ou None."""
    for motif in (_BATI_HABITABLE, _BATI_SURFACE, _BATI_BIEN):
        for m in motif.finditer(texte):
            if _PAS_DU_BATI.search(_amont(texte, m.start(1))):
                continue
            s = _surface(m.group(1))
            if s is not None and _BATI_MIN <= s <= _BATI_MAX:
                return s
    return None


# --- Complétion d'un bien -------------------------------------------------------------

# Ordre d'écriture. Les pièces AVANT les chambres : elles plafonnent le compte lu dans
# le texte (voir `_plafond_chambres`). `terrain` n'est PAS déduit du type de bien — un
# appartement peut avoir un jardin privatif, et le catalogue en a.
CHAMPS = ("nb_pieces", "nb_chambres", "surface_terrain", "surface_bati")

_LECTEURS = {"nb_chambres": chambres, "nb_pieces": pieces,
             "surface_terrain": terrain, "surface_bati": bati}


def lire(texte: str) -> dict:
    """Tout ce que le texte dit des champs structurels (sans référence à un bien)."""
    return {champ: v for champ, lecteur in _LECTEURS.items()
            if (v := lecteur(texte)) is not None}


def _plafond_chambres(lu: int, nb_pieces) -> int:
    """Un logement de N pièces a au plus N-1 chambres : la pièce de vie en est une.

    Vrai sur 99 % des 6 221 biens dont la source donne les deux. Le plafond ne s'applique
    qu'à un compte LU DANS LE TEXTE, et il ne peut que le baisser. Il ferme le seul
    dérapage du comptage par niveau : une annonce qui décrit la maison PUIS son annexe
    louée (« deux chambres [...] le studio comprend deux chambres ») additionne deux
    logements. Mesuré : les surestimations passent de 5 % à 3 % des comptes lus.
    """
    if not nb_pieces:
        return lu
    return min(lu, max(1, int(nb_pieces) - 1))


def completer(item, texte_source: str | None = None) -> dict:
    """Complète les champs NULS de `item` depuis son texte, sans jamais en corriger un.

    `item` est un `NormalizedListing` (collecte) ou un `Listing` (base) : mêmes noms de
    champs. Renvoie `{champ: valeur}` pour ce qui a été écrit — la provenance, à
    journaliser.

    `texte_source` : un texte À LIRE qu'on ne veut pas STOCKER. C'est le cas du corps
    d'une fiche d'agence — 3 500 à 12 800 caractères de navigation où se trouvent les
    seules mentions des chambres et du terrain, mesuré sur des fiches réelles : ni le
    titre ni `og:description` ne les portent jamais. On y lit les champs sans en faire
    la description du bien, qui resterait illisible sur le site.
    """
    texte = normalize(texte_source) if texte_source else normalize(
        getattr(item, "adresse", None), getattr(item, "description", None))
    if not texte:
        return {}
    ecrits: dict = {}
    for champ in CHAMPS:
        if getattr(item, champ, None) is not None:
            continue
        valeur = _LECTEURS[champ](texte)
        if valeur is None:
            continue
        if champ == "nb_chambres":
            valeur = _plafond_chambres(valeur, getattr(item, "nb_pieces", None))
        setattr(item, champ, valeur)
        ecrits[champ] = valeur
    return ecrits
