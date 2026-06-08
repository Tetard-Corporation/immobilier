"""Ingestion de la source "agences" : newsletters email + sites d'agences.

Flux : relève IMAP + scraping des sites configurés -> extraction (LLM/heuristique)
-> normalisation -> annotation (état/qualité) -> persistance (source="agences").
Les annonces ingérées alimentent ensuite la recherche et les nouveautés via
`AgencesSource`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from urllib.parse import urljoin

import httpx

from ..agences_config import load_agences_config
from ..config import get_settings
from ..sources.base import NormalizedListing
from ..sources.htmlutil import json_ld_items, realestate_fields
from .agences_parsers import parse_site
from .email_ingest import fetch_unseen
from .enrich import annotate
from .extract import get_extractor
from .search import upsert_listing

logger = logging.getLogger("immobilier.agences")

_UA = {"User-Agent": "Mozilla/5.0 (compatible; ImmobilierBot/0.1)"}


def _external_id(agency: str, url: str | None, d: dict) -> str:
    if url:
        seed = url
    else:
        seed = f"{agency}|{d.get('type_bien')}|{d.get('prix')}|{d.get('code_postal')}|{(d.get('description') or '')[:60]}"
    return "ag_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:14]


def _to_normalized(d: dict, agency: str) -> NormalizedListing:
    url = d.get("url")
    raw = dict(d)
    raw["agence"] = agency
    return NormalizedListing(
        source="agences",
        external_id=_external_id(agency, url, d),
        type_bien=d.get("type_bien"),
        prix=d.get("prix"),
        surface_terrain=d.get("surface_terrain"),
        surface_bati=d.get("surface_bati"),
        commune=d.get("commune"),
        code_postal=d.get("code_postal"),
        url=url,
        description=d.get("description"),
        flags={},
        raw=raw,
    )


_DETAIL_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
_LOC = r"([A-ZÉÈÀ][\wÀ-ÿ'\-]+(?:[ \-](?:la|le|les|sur|sous|en|d['’]|de|du)?[ \-]?[A-ZÉÈÀ]?[\wÀ-ÿ'\-]+){0,3})"
_COMMUNE_RE = re.compile(rf"(?:village|commune|hameau|bourg|proche(?:\s+de)?|à)\s+(?:de\s+|d['’]\s*)?{_LOC}")


def _og(html: str, prop: str) -> str | None:
    m = (re.search(rf'<meta[^>]+property="{prop}"[^>]+content="([^"]+)"', html)
         or re.search(rf'<meta[^>]+content="([^"]+)"[^>]+property="{prop}"', html))
    return m.group(1) if m else None


def _enrich_from_detail(nl: NormalizedListing) -> NormalizedListing:
    """Complète depuis la page détail : garantit la PHOTO (og:image) et, si manquants,
    le prix (class="prix") et la commune (og:title/description). Une seule requête, et
    seulement si quelque chose manque -> pas de surcoût pour les agences déjà complètes."""
    need_photo = not ((nl.raw or {}).get("photos"))
    need_commune = not nl.commune
    need_price = nl.prix is None
    if not nl.url or not (need_photo or need_commune or need_price):
        return nl
    try:
        html = httpx.get(nl.url, headers=_DETAIL_UA, timeout=20, follow_redirects=True).text
    except Exception:
        return nl
    if need_photo and (img := _og(html, "og:image")):
        nl.raw = {**(nl.raw or {}), "photos": [img]}
    if need_price:
        pm = re.search(r'class="prix.*?(\d[\d\s ]{2,})\s*(?:€|&euro;)', html, re.S)
        if pm:
            digits = re.sub(r"[^\d]", "", pm.group(1))
            nl.prix = float(digits) if digits else None
    if need_commune:
        m = _COMMUNE_RE.search(_og(html, "og:title") or "") or _COMMUNE_RE.search(_og(html, "og:description") or "")
        if m:
            nl.commune = m.group(1).strip()
    return nl


def _fill_geo(nl: NormalizedListing) -> NormalizedListing:
    """Résout la commune (souvent un titre libre) via la BAN -> commune canonique +
    dept/coords, pour rendre les biens d'agences exploitables (filtre dept, carte,
    scoring). Étape réseau, séparée de la normalisation (pure)."""
    if nl.commune and nl.latitude is None:
        from .geo import geocode_locality

        g = geocode_locality(nl.commune)
        if g:
            nl.commune = g["nom"]
            nl.latitude, nl.longitude = g["lat"], g["lon"]
            nl.code_postal = nl.code_postal or g["code_postal"]
            nl.code_commune = nl.code_commune or g["code_commune"]
            nl.departement = nl.departement or g["departement"]
    return nl


def scrape_sites(site_urls: list[tuple[str, str]], settings=None) -> list[NormalizedListing]:
    """Scrape les pages d'annonces d'agences (JSON-LD prioritaire)."""
    settings = settings or get_settings()
    items: list[NormalizedListing] = []
    with httpx.Client(headers=_UA, timeout=settings.http_timeout_seconds, follow_redirects=True) as client:
        for agency, url in site_urls:
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except Exception as exc:  # un site KO ne bloque pas les autres
                logger.warning("Site agence injoignable %s : %s", url, exc)
                continue
            found = False
            for obj in json_ld_items(resp.text):
                f = realestate_fields(obj)
                if not f or f.get("price") is None:
                    continue
                found = True
                items.append(_fill_geo(_enrich_from_detail(_to_normalized(
                    {
                        "type_bien": None,
                        "prix": f.get("price"),
                        "surface_bati": f.get("surface"),
                        "surface_terrain": None,
                        "commune": f.get("city"),
                        "code_postal": f.get("postal_code"),
                        "url": urljoin(url, f.get("url") or url),
                        "description": f.get("description") or f.get("name"),
                    },
                    agency,
                ))))
            # Pas de JSON-LD exploitable -> repli sur un parser HTML dédié à l'agence.
            if not found:
                for d in parse_site(url, resp.text):
                    # On accepte un bien sans prix de carte s'il a une URL (le détail le
                    # remplira) ; on écarte ensuite ceux dont le prix reste introuvable.
                    if d.get("prix") is None and not d.get("url"):
                        continue
                    nl = _fill_geo(_enrich_from_detail(_to_normalized(d, agency)))
                    if nl.prix is not None:
                        items.append(nl)
    return items


def ingest(db, settings=None) -> dict:
    """Relève emails + sites, extrait, annote et persiste. Renvoie un récap."""
    settings = settings or get_settings()
    config = load_agences_config(settings.agences_config_path)
    extractor = get_extractor()

    collected: list[NormalizedListing] = []

    # 1) Emails
    for mail in fetch_unseen(settings):
        for d in extractor.extract(mail.subject, mail.body, is_html=mail.is_html):
            collected.append(_fill_geo(_enrich_from_detail(_to_normalized(d, agency=mail.sender or "Email"))))

    # 2) Sites d'agences
    collected.extend(scrape_sites(config.all_site_urls, settings))

    # 3) Normalisation + persistance
    nb = 0
    for item in collected:
        upsert_listing(db, annotate(item))
        nb += 1
    db.commit()
    logger.info("Ingestion agences : %s annonce(s) traitée(s) (extracteur=%s).", nb, extractor.name)
    return {"ingested": nb, "extractor": extractor.name}
