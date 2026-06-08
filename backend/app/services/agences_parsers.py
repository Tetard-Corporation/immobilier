"""Parsers HTML par agence (Voie B) pour les sites locaux sans JSON-LD.

Chaque parser prend (html, base_url) et renvoie une liste de dicts d'annonces
(mêmes clés que l'extracteur : type_bien, prix, surface_bati, surface_terrain,
commune, code_postal, url, description). Enregistrés par domaine, ils sont utilisés
par `scrape_sites` en repli quand la page n'expose pas de JSON-LD schema.org.

Ajouter une agence = écrire un parser + l'enregistrer dans SITE_PARSERS + lister
son/ses URL(s) de pages d'annonces dans agences.yaml.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

_TAGS = re.compile(r"<[^>]+>")


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", _TAGS.sub(" ", s)).strip()


def _num(s: str | None) -> float | None:
    digits = re.sub(r"[^\d]", "", s or "")
    return float(digits) if digits else None


def _type_from_title(title: str | None) -> str:
    t = (title or "").lower()
    if "terrain" in t:
        return "terrain"
    if "appartement" in t:
        return "appartement"
    if "immeuble" in t:
        return "immeuble"
    return "maison"  # maison, ferme, grange, mas, propriété en pierres…


def parse_agence_cevenole(html: str, base_url: str) -> list[dict]:
    """agencecevenole.com : cartes délimitées par <h2 class="headline-ann">."""
    out: list[dict] = []
    for block in re.split(r'(?=<h2 class="headline-ann)', html)[1:]:
        href = re.search(r'href="(details-[^"]+)"', block)
        if not href:
            continue
        tm = re.search(r'title="([^"]+)"', block)
        title = tm.group(1).strip() if tm else ""
        txt = _text(block)
        # Prix lu dans le bloc .prix (sinon la réf "990" se colle au prix).
        price = re.search(r'class="prix".*?(\d[\d\s ]{2,})\s*€', block, re.S)
        if not price:
            continue
        hab = re.search(r"Surface habitable\s+([\d\s ]+)", txt)
        ter = re.search(r"Surface terrain\s+([\d\s ]+)", txt)
        desc = re.search(r"m²\s*(.+?)\s*En savoir plus", txt)
        out.append({
            "type_bien": _type_from_title(title),
            "prix": _num(price.group(1)),
            "surface_bati": _num(hab.group(1)) if hab else None,
            "surface_terrain": _num(ter.group(1)) if ter else None,
            "commune": title,  # titre libre -> commune canonique résolue via la BAN
            "code_postal": None,
            "url": urljoin(base_url, href.group(1)),
            "description": (desc.group(1).strip() if desc else title) or None,
        })
    return out


# Domaine (sans www.) -> parser.
SITE_PARSERS = {
    "agencecevenole.com": parse_agence_cevenole,
}


def parse_site(url: str, html: str) -> list[dict]:
    """Dispatch vers le parser enregistré pour le domaine de l'URL (sinon [])."""
    host = (urlparse(url).hostname or "").removeprefix("www.")
    fn = SITE_PARSERS.get(host)
    return fn(html, url) if fn else []
