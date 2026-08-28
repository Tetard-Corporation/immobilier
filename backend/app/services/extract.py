"""Extraction d'annonces depuis le texte d'une newsletter d'agence.

Deux implémentations derrière une interface commune :
- LLMExtractor : API Claude (modèle Haiku), sortie structurée + prompt caching.
  Dépendance optionnelle (`anthropic`) ; activée si ANTHROPIC_API_KEY est présent.
- HeuristicExtractor : repli par expressions régulières, sans clé ni réseau.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..config import get_settings
from ..sources.htmlutil import html_to_text


class ExtractedListing(BaseModel):
    """Une annonce extraite d'un email (champs absents = null)."""

    type_bien: str | None = Field(
        default=None, description="terrain, maison, appartement, immeuble, local_commercial, parking"
    )
    prix: float | None = Field(default=None, description="Prix en euros")
    surface_terrain: float | None = Field(default=None, description="Surface du terrain en m²")
    surface_bati: float | None = Field(default=None, description="Surface habitable en m²")
    commune: str | None = None
    code_postal: str | None = None
    url: str | None = Field(default=None, description="Lien vers l'annonce si présent")
    description: str | None = None


class ExtractedListings(BaseModel):
    listings: list[ExtractedListing] = Field(default_factory=list)


_SYSTEM = """Tu es un extracteur d'annonces immobilières à partir d'emails d'agences.

On te fournit le texte (ou HTML converti) d'une newsletter d'agence immobilière, qui
peut contenir zéro, une ou plusieurs annonces de biens à vendre (terrains, maisons,
appartements, immeubles, locaux, etc.).

Extrais CHAQUE annonce distincte sous forme structurée :
- type_bien : un parmi terrain, maison, appartement, immeuble, local_commercial, parking
  (déduis-le du texte ; null si vraiment indéterminable).
- prix : nombre en euros (sans symbole ni séparateur), null si absent.
- surface_terrain / surface_bati : en m², null si absent.
- commune, code_postal : localisation.
- url : le lien direct vers l'annonce s'il existe.
- description : un court résumé (1-2 phrases) reprenant les caractéristiques utiles.

Règles :
- N'invente jamais une valeur : laisse null si l'information n'est pas présente.
- Ignore les contenus non immobiliers (signatures, mentions légales, désinscription).
- Une seule annonce par bien ; ne duplique pas.
- Si l'email ne contient aucune annonce, renvoie une liste vide."""


class Extractor:
    name = "base"

    def extract(self, subject: str | None, body: str | None, is_html: bool = False) -> list[dict]:
        raise NotImplementedError


class HeuristicExtractor(Extractor):
    """Repli sans LLM : repère prix / surface / code postal / 1er lien."""

    name = "heuristic"
    # Les séparateurs de milliers vus en vrai : espace, espace insécable (U+00A0) et
    # espace fine insécable (U+202F, celle des sites récents). Oublier la fine faisait
    # lire « 1 189 000 € » comme 0.
    _ESPACES = " \u00a0\u202f\u2009"
    _PRICE = re.compile(rf"(\d[\d{_ESPACES}.]{{2,}})\s*\u20ac")
    _SURFACE = re.compile(rf"(\d[\d{_ESPACES}.]*)\s*m(?:\u00b2|2)\b", re.I)
    _CP = re.compile(r"\b(\d{5})\b")
    _URL = re.compile(r"https?://[^\s)\"']+")
    _TYPES = ["terrain", "maison", "appartement", "immeuble", "local", "parking",
              "villa", "propri\u00e9t\u00e9", "propriete", "manoir", "long\u00e8re", "longere",
              "ferme", "moulin", "ch\u00e2teau", "chateau", "h\u00f4tel particulier",
              "hotel particulier", "demeure", "studio", "loft"]
    # Vers quel type normalis\u00e9 bascule chaque mot ci-dessus.
    _TYPE_CANON = {"local": "local_commercial", "villa": "maison", "propri\u00e9t\u00e9": "maison",
                   "propriete": "maison", "manoir": "maison", "long\u00e8re": "maison",
                   "longere": "maison", "ferme": "maison", "moulin": "maison",
                   "ch\u00e2teau": "maison", "chateau": "maison", "demeure": "maison",
                   "h\u00f4tel particulier": "maison", "hotel particulier": "maison",
                   "studio": "appartement", "loft": "appartement"}

    @staticmethod
    def _to_float(raw: str) -> float | None:
        cleaned = raw
        for c in HeuristicExtractor._ESPACES:
            cleaned = cleaned.replace(c, "")
        cleaned = cleaned.replace(".", "")
        try:
            return float(cleaned)
        except ValueError:
            return None

    # Montants en euros qui ne sont PAS le prix de vente. Vu en vrai : une fiche affichait
    # « Taxe foncière : 571 € » avant son prix, et le bien entrait en base à 571 € — donc
    # imbattable au budget et au €/m², donc pépite. Un prix faux est pire qu'un prix absent.
    _PAS_UN_PRIX = re.compile(
        r"(?:taxe|fonci[èe]re|habitation|honoraires?|charges?|copropri[ée]t[ée]|loyer|"
        r"mensualit[ée]|frais|commission|d[ée]p[ôo]t|garantie|caution|estimation\s+des\s+co[ûu]ts|"
        r"consommation|[ée]nerg|par\s+an|/\s*an|/\s*mois|au\s+m|du\s+m|par\s+m)",
        re.I)
    # Le DÉCOR du site : bornes de son curseur de recherche, garantie financière,
    # capital social. Ces montants sont souvent PLUS GROS que le prix réel, donc ils
    # gagnent contre lui (on retient le plus grand montant plausible). Vu en vrai : un
    # site du Diois dont les 45 biens, à 44 000-128 000 €, entraient tous à 1 000 000 €
    # — la borne haute de son formulaire « Prix compris entre 0 € et 1 000 000 € ».
    #
    # On disqualifie la VALEUR annoncée, pas ce qui est proche d'elle : un filtre de
    # proximité écartait aussi le vrai prix quand la page le plaçait sous le formulaire.
    # Les séparateurs admis entre les morceaux ne sont pas que des espaces : la mise en
    # page insère « : », des puces, des retours à la ligne (« entre : / 0 € / et / 1000000 € »).
    _SEP = r"[\s:/|•·\-–>]*"
    _BORNES = re.compile(
        rf"(?:compris{_SEP}entre|entre|de){_SEP}([\d][\d\s.,]*)\s*€{_SEP}(?:et|[àa]){_SEP}([\d][\d\s.,]*)\s*€",
        re.I)
    # Ces étiquettes-là collent à leur montant : fenêtre courte, retours à la ligne aplatis.
    _DECOR_ETIQUETTE = re.compile(
        r"garantie\s+financi[èe]re|capital\s+social|chiffre\s+d[' ]affaires", re.I)

    _PRIX_MIN = 10_000        # sous ce seuil, ce n'est pas le prix d'un bien
    _PRIX_MAX = 30_000_000

    @classmethod
    def _prix(cls, texte: str) -> float | None:
        """Prix de vente : le plus grand montant plausible dont le contexte ne le
        disqualifie pas.

        Prendre le PREMIER montant de la page était le réflexe naturel et il est faux :
        taxe foncière, honoraires et charges apparaissent souvent avant le prix. Le prix
        de vente est en pratique le plus gros montant légitime d'une fiche.
        """
        # Bornes du formulaire de recherche : ces valeurs précises sont du décor.
        decor = set()
        for b in cls._BORNES.finditer(texte or ""):
            for brut in b.groups():
                v = cls._to_float(brut)
                if v is not None:
                    decor.add(v)

        candidats = []
        for m in cls._PRICE.finditer(texte or ""):
            # Le contexte s'arrête à la ligne (ou la phrase) courante : remonter au-delà
            # attrapait l'étiquette du montant PRÉCÉDENT et disqualifiait le bon prix.
            debut = max(0, m.start() - 60)
            contexte = texte[debut:m.start()]
            coupe = max(contexte.rfind(c) for c in "\n.|;•\t")
            if coupe >= 0:
                contexte = contexte[coupe + 1:]
            if cls._PAS_UN_PRIX.search(contexte):
                continue
            etiquette = (texte[max(0, m.start() - 60):m.start()]
                         .replace("\n", " ").replace("\t", " "))
            if cls._DECOR_ETIQUETTE.search(etiquette):
                continue
            v = cls._to_float(m.group(1))
            if v is not None and v not in decor and cls._PRIX_MIN <= v <= cls._PRIX_MAX:
                candidats.append(v)
        return max(candidats) if candidats else None

    @classmethod
    def _type_bien(cls, titre: str, texte: str) -> str | None:
        """Type du bien, cherch\u00e9 dans le TITRE d'abord, puis \u00e0 la premi\u00e8re occurrence.

        Balayer la liste dans l'ordre sur toute la page renvoyait \u00ab terrain \u00bb pour \u00e0 peu
        pr\u00e8s tout : le mot tra\u00eene dans les menus et les pieds de page de la plupart des
        sites. Un h\u00f4tel particulier \u00e9tait class\u00e9 terrain.
        """
        for source in (titre, texte):
            trouves = [(m.start(), t) for t in cls._TYPES
                       if (m := re.search(rf"\b{re.escape(t)}", source or "", re.I))]
            if trouves:
                mot = min(trouves)[1]
                return cls._TYPE_CANON.get(mot.lower(), mot.lower())
        return None

    def extract(self, subject: str | None, body: str | None, is_html: bool = False) -> list[dict]:
        text = html_to_text(body) if is_html else (body or "")
        full = f"{subject or ''}\n{text}"
        if not full.strip():
            return []
        price = self._prix(full)
        surface = self._SURFACE.search(full)
        cp = self._CP.search(full)
        url = self._URL.search(full)
        type_bien = self._type_bien(subject or "", text)
        if not (price or surface or cp):
            return []
        return [
            {
                "type_bien": type_bien,
                "prix": price,
                "surface_terrain": self._to_float(surface.group(1))
                if surface and type_bien == "terrain"
                else None,
                "surface_bati": self._to_float(surface.group(1))
                if surface and type_bien != "terrain"
                else None,
                "commune": None,
                "code_postal": cp.group(1) if cp else None,
                "url": url.group(0) if url else None,
                "description": (subject or text[:200]).strip(),
            }
        ]


class LLMExtractor(Extractor):
    """Extraction par l'API Claude (Haiku) : sortie structurée + prompt caching."""

    name = "llm"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic  # import paresseux (dépendance optionnelle)

            self._client = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
        return self._client

    def extract(self, subject: str | None, body: str | None, is_html: bool = False) -> list[dict]:
        text = html_to_text(body) if is_html else (body or "")
        if not text.strip():
            return []
        user = f"Objet : {subject or ''}\n\nContenu :\n{text[:50000]}"
        resp = self._get_client().messages.parse(
            model=self._settings.extract_model,
            max_tokens=8192,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_format=ExtractedListings,
        )
        parsed = resp.parsed_output
        if parsed is None:
            return []
        return [item.model_dump() for item in parsed.listings]


def get_extractor() -> Extractor:
    """LLM si une clé Claude est configurée ; sinon repli heuristique."""
    settings = get_settings()
    if settings.llm_extract_available:
        try:
            import anthropic  # noqa: F401

            return LLMExtractor()
        except ImportError:
            pass
    return HeuristicExtractor()
