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
        price = self._PRICE.search(full)
        surface = self._SURFACE.search(full)
        cp = self._CP.search(full)
        url = self._URL.search(full)
        type_bien = self._type_bien(subject or "", text)
        if not (price or surface or cp):
            return []
        return [
            {
                "type_bien": type_bien,
                "prix": self._to_float(price.group(1)) if price else None,
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
