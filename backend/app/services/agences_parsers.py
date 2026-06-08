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
    """agencecevenole.com : cartes <div class="ann ..."> (image + titre + prix)."""
    out: list[dict] = []
    for block in re.split(r'(?=<div class="ann )', html)[1:]:
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
        img = re.search(r'(?:src|data-src)="(public/img/[^"]+\.(?:jpe?g|png|webp))"', block, re.I)
        out.append({
            "type_bien": _type_from_title(title),
            "prix": _num(price.group(1)),
            "surface_bati": _num(hab.group(1)) if hab else None,
            "surface_terrain": _num(ter.group(1)) if ter else None,
            "commune": title,  # titre libre -> commune canonique résolue via la BAN
            "code_postal": None,
            "url": urljoin(base_url, href.group(1)),
            "description": (desc.group(1).strip() if desc else title) or None,
            "photos": [urljoin(base_url, img.group(1))] if img else [],
        })
    return out


def parse_bauges_immobilier(html: str, base_url: str) -> list[dict]:
    """bauges-immobilier.com (CRM Cello) : <li data-property-id> avec h3 'Type, Commune',
    li.price, surface en m², image cloudfront. Couvre le massif des Bauges (Savoie)."""
    out: list[dict] = []
    for block in re.split(r'(?=<li[^>]*data-property-id=")', html)[1:]:
        href = re.search(r'href="(/fr/propriete/[^"]+)"', block)
        price = re.search(r'class="price">\s*(\d[\d\s ]{2,})\s*€', block)
        if not href or not price:
            continue
        h3 = re.search(r"<h3>([^<]+)</h3>", block)
        h2 = re.search(r"<h2>([^<]+)</h2>", block)
        area = re.search(r"(\d[\d\s ]*)\s*m²", block)
        img = re.search(r'<img[^>]+src="(https?://[^"]+\.(?:jpe?g|png|webp)[^"]*)"', block, re.I)
        label = h3.group(1).strip() if h3 else ""
        type_label, _, commune = label.partition(",")
        out.append({
            "type_bien": _type_from_title(type_label),
            "prix": _num(price.group(1)),
            "surface_bati": _num(area.group(1)) if area else None,
            "surface_terrain": None,
            "commune": commune.strip() or label or None,
            "code_postal": None,
            "url": urljoin(base_url, href.group(1)),
            "description": (h2.group(1).strip() if h2 else label) or None,
            "photos": [img.group(1)] if img else [],
        })
    return out


# Domaine (sans www.) -> parser.
SITE_PARSERS = {
    "agencecevenole.com": parse_agence_cevenole,
    "bauges-immobilier.com": parse_bauges_immobilier,
}


def parse_site(url: str, html: str) -> list[dict]:
    """Dispatch vers le parser enregistré pour le domaine de l'URL (sinon [])."""
    host = (urlparse(url).hostname or "").removeprefix("www.")
    fn = SITE_PARSERS.get(host)
    return fn(html, url) if fn else []
