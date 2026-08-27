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
import time
from urllib.parse import urljoin

import httpx

from ..agences_config import load_agences_config
from ..config import get_settings
from ..sources.base import NormalizedListing
from ..sources.htmlutil import json_ld_items, realestate_fields
from .agences_parsers import harvest_detail_links, parse_site
from .email_ingest import fetch_unseen
from .enrich import annotate
from .extract import get_extractor
from .search import upsert_listing

logger = logging.getLogger("immobilier.agences")

_UA = {"User-Agent": "Mozilla/5.0 (compatible; ImmobilierBot/0.1)"}


def _EXTRACTEUR():
    """Extracteur d'annonces (LLM si clé Anthropic, sinon heuristique). Résolu à l'appel
    pour que les tests puissent l'injecter."""
    return get_extractor()


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


# Titre d'annonce d'agence, format quasi universel : « Vente maison Henvic 6 pièces 120m² ».
# La commune est ce qui suit le type de bien et précède le premier chiffre.
_TITRE_COMMUNE_RE = re.compile(
    r"\b(?:maison|appartement|terrain|immeuble|villa|propri[ée]t[ée]|ferme|longère|"
    r"manoir|moulin|ch[âa]teau|local|studio|loft|corps\s+de\s+ferme)\s+"
    r"(?:[àa]\s+|de\s+|d['’]\s*)?"
    r"([A-ZÉÈÀÎÔÜ][\wÀ-ÿ'’\-]*(?:[ \-](?:la|le|les|sur|sous|en|lès|les|de|du|des|d['’])?[ \-]?"
    r"[A-ZÉÈÀÎÔÜ]?[\wÀ-ÿ'’\-]+){0,3}?)"
    r"(?=\s+\d|\s*[,|·–—]|\s*$)", re.I)


# Mots qui disqualifient une capture : soit ils situent le bien PAR RAPPORT à une ville
# sans le placer dedans (« proche Morlaix » n'est pas Morlaix), soit ils décrivent le bien
# (« maison de plain-pied »). Dans les deux cas mieux vaut aucune commune qu'une fausse :
# une commune erronée géocode, donc produit des coordonnées fausses qu'aucune étape ne
# rattrape ensuite.
_MOTS_DISQUALIFIANTS = {
    "proche", "proches", "près", "pres", "secteur", "environs", "alentours", "axe",
    "plain-pied", "plainpied", "centre", "bourg", "campagne", "vue", "bord",
}


def commune_depuis_titre(titre: str | None) -> str | None:
    """Commune lue dans un titre d'annonce. None si le titre n'en porte pas de sûre.

    Sans commune, `_fill_geo` ne peut pas géocoder, donc le bien n'a ni coordonnées ni
    département : il est invisible pour les filtres de zone et le scoring. C'est le champ
    qui décide si une annonce d'agence est exploitable — d'où le soin mis à ne pas en
    inventer une.
    """
    m = _TITRE_COMMUNE_RE.search(titre or "")
    if not m:
        return None
    nom = re.sub(r"\s+", " ", m.group(1)).strip(" -,")
    if not nom or not nom[:1].isupper():
        return None
    if any(mot.lower().strip(",.") in _MOTS_DISQUALIFIANTS for mot in nom.split()):
        return None
    return nom


def _og(html: str, prop: str) -> str | None:
    m = (re.search(rf'<meta[^>]+property="{prop}"[^>]+content="([^"]+)"', html)
         or re.search(rf'<meta[^>]+content="([^"]+)"[^>]+property="{prop}"', html))
    return m.group(1) if m else None


def _enrich_from_detail(nl: NormalizedListing, html: str | None = None) -> NormalizedListing:
    """Complète depuis la page détail : garantit la PHOTO (og:image) et, si manquants,
    le prix (class="prix") et la commune (og:title/description). Une seule requête, et
    seulement si quelque chose manque -> pas de surcoût pour les agences déjà complètes.

    `html` : page déjà téléchargée. La voie générique l'a en main, et la redemander
    doublait le nombre de requêtes envoyées aux sites d'agences."""
    need_photo = not ((nl.raw or {}).get("photos"))
    need_commune = not nl.commune
    need_price = nl.prix is None
    if not nl.url or not (need_photo or need_commune or need_price):
        return nl
    if html is None:
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
        titre = _og(html, "og:title") or ""
        desc = _og(html, "og:description") or ""
        m = _COMMUNE_RE.search(titre) or _COMMUNE_RE.search(desc)
        # Repli sur le format de titre d'agence (« Vente maison Henvic 6 pièces »), que
        # _COMMUNE_RE ne voit pas : il attend « à X » / « commune de X ».
        nl.commune = (m.group(1).strip() if m
                      else commune_depuis_titre(titre) or commune_depuis_titre(desc)
                      or commune_depuis_titre(nl.description))
    return nl


def _fill_geo(nl: NormalizedListing) -> NormalizedListing:
    """Résout la commune (souvent un titre libre) via la BAN -> commune canonique +
    dept/coords, pour rendre les biens d'agences exploitables (filtre dept, carte,
    scoring). Étape réseau, séparée de la normalisation (pure)."""
    # Pas de commune mais un code postal : les fiches d'agence portent presque toujours
    # le CP (« à LORIENT (56100) »), et lui seul suffit. Sur un CP rural qui couvre
    # plusieurs communes on prend la plus peuplée, comme la source SeLoger.
    if not nl.commune and nl.code_postal:
        from .geo_communes import main_commune_for_postcode

        c = main_commune_for_postcode(nl.code_postal)
        if c:
            nl.commune = c.get("nom")
            nl.code_commune = nl.code_commune or c.get("code")
            if nl.latitude is None:
                nl.latitude, nl.longitude = c.get("lat"), c.get("lon")
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
                    found = True
            # Toujours rien -> voie générique : suivre les fiches et lire LEUR JSON-LD.
            if not found:
                items.extend(_scrape_via_fiches(client, agency, url, resp.text, settings))
    return items


def _scrape_via_fiches(client, agency: str, url: str, html: str, settings,
                       cap: int = 40) -> list[NormalizedListing]:
    """Voie générique : la page de liste n'a pas de JSON-LD, les fiches en ont un.

    C'est le cas courant chez les agences locales — mesuré sur les agences bretonnes :
    zéro bien sur la liste, un bien avec prix sur chaque fiche. On récolte donc les liens
    de fiches et on lit le JSON-LD de chacune, sans rien coder de spécifique au site.

    La récolte est permissive et le tri se fait ici : une page de rubrique ramassée par
    erreur n'a pas de JSON-LD immobilier avec prix, donc elle tombe d'elle-même.
    """
    liens = harvest_detail_links(html, url, max_links=cap)
    if not liens:
        return []
    delai = max(0, getattr(settings, "scraper_rate_limit_ms", 2000)) / 1000
    trouves: list[NormalizedListing] = []
    for i, lien in enumerate(liens):
        if i:
            time.sleep(delai)  # site d'agence = petit serveur, on y va doucement
        try:
            page = client.get(lien)
            page.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Fiche agence injoignable %s : %s", lien, exc)
            continue
        fiche = None
        for obj in json_ld_items(page.text):
            f = realestate_fields(obj)
            if not f or f.get("price") is None:
                continue
            fiche = {
                "type_bien": None,
                "prix": f.get("price"),
                "surface_bati": f.get("surface"),
                "surface_terrain": None,
                "commune": f.get("city"),
                "code_postal": f.get("postal_code"),
                "url": lien,
                "description": f.get("description") or f.get("name"),
                "_geo": (f.get("latitude"), f.get("longitude")),
            }
            break  # une fiche = un bien

        # Voie D — la fiche n'a aucun JSON-LD immobilier : on lit son texte. Mesuré sur
        # les agences de prestige, c'est le cas le plus courant (safti-prestige, espaces
        # atypiques, orpi) : le prix et le code postal sont en clair dans la page, seule
        # la donnée structurée manque.
        if fiche is None:
            for d in _EXTRACTEUR().extract(_og(page.text, "og:title") or lien,
                                           page.text, is_html=True):
                if d.get("prix"):
                    fiche = {**d, "url": lien, "_geo": (None, None)}
                    break

        if fiche is None:
            continue
        lat, lon = fiche.pop("_geo", (None, None))
        nl = _to_normalized(fiche, agency)
        if lat is not None:
            nl.latitude, nl.longitude = lat, lon
        nl = _fill_geo(_enrich_from_detail(nl, page.text))
        if nl.prix is not None and nl.commune:
            trouves.append(nl)
    logger.info("Agence %s : %s bien(s) via fiches (%s liens suivis).",
                agency, len(trouves), len(liens))
    return trouves


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
    # Rattachement au set déclaré par l'agence : sans lui, `set_ids` reste vide et
    # l'export note le bien pour TOUS les sets (rétro-compat) — une agence bretonne
    # apparaîtrait dans le set Drôme/Ardèche.
    sets = config.set_par_agence
    nb = 0
    for item in collected:
        row = upsert_listing(db, annotate(item))
        set_id = sets.get((item.raw or {}).get("agence"))
        if set_id is not None and set_id not in (row.set_ids or []):
            row.set_ids = sorted({*(row.set_ids or []), set_id})
        nb += 1
    db.commit()
    logger.info("Ingestion agences : %s annonce(s) traitée(s) (extracteur=%s).", nb, extractor.name)
    return {"ingested": nb, "extractor": extractor.name}
