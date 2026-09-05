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


def parse_christine_miranda(html: str, base_url: str) -> list[dict]:
    """christinemiranda.com (CMS jalik, biens de caractère à rénover en Drôme/Vaucluse).
    Prix et commune sont dans des conteneurs séparés sur la liste -> on n'extrait que le
    lien détail (fiable) ; prix/commune/photo sont complétés depuis la page détail."""
    out: list[dict] = []
    seen: set[str] = set()
    for link in re.findall(r'href="(details-[^"]+)"', html):
        if link in seen:
            continue
        seen.add(link)
        label = link.removeprefix("details-").rsplit("-", 1)[0].replace("+", " ")
        out.append({
            "type_bien": _type_from_title(label),
            "prix": None,            # complété depuis la page détail (class="prix")
            "surface_bati": None,
            "surface_terrain": None,
            "commune": None,         # complété depuis la page détail (og:title)
            "code_postal": None,
            "url": urljoin(base_url, link),
            "description": label or None,
            "photos": [],            # garantie par og:image
        })
    return out


# Domaine (sans www.) -> parser.
SITE_PARSERS = {
    "agencecevenole.com": parse_agence_cevenole,
    "bauges-immobilier.com": parse_bauges_immobilier,
    "christinemiranda.com": parse_christine_miranda,
}


def parse_site(url: str, html: str) -> list[dict]:
    """Dispatch vers le parser enregistré pour le domaine de l'URL (sinon [])."""
    host = (urlparse(url).hostname or "").removeprefix("www.")
    fn = SITE_PARSERS.get(host)
    return fn(html, url) if fn else []


# --------------------------------------------------------------------------- #
# Voie C — générique : suivre les liens de détail et y lire le JSON-LD.
#
# Beaucoup de sites d'agences ne mettent aucun JSON-LD sur la page de LISTE mais en
# posent un propre (RealEstateListing, Product) sur chaque page de BIEN. Mesuré sur les
# agences bretonnes : liste 0 bien, détail 1 bien avec prix. Cette voie récupère donc
# les liens de détail de la liste, sans rien savoir du site, et laisse le JSON-LD de
# chaque fiche faire le travail. Elle évite d'écrire un parser par agence.
# --------------------------------------------------------------------------- #

# Un lien de détail porte presque toujours l'un de ces mots, et un identifiant. Le mot
# n'est pas exigé en début de segment : « /a-vendre-belle-propriete-… » est un format
# courant, et le chercher seulement après « / » le manquait.
_DETAIL_MOTS = re.compile(
    r"(?:vente|vendre|annonce|bien|propriete|maison|terrain|appartement|detail|offre)",
    re.I)
_A_HREF = re.compile(r'<a\b[^>]*href=["\']([^"\'#]+)["\']', re.I)

# Pages de service à ne jamais confondre avec un bien.
_DETAIL_EXCLU = re.compile(
    r"/(?:estimation|estimer|contact|mentions|cgv|cgu|blog|actualite|equipe|agence|"
    r"honoraires|recrutement|vendu|nos-dernieres-ventes|login|panier)", re.I)


_CODE_POSTAL_RE = re.compile(r"\b\d{5}\b")


def _ressemblance_fiche(chemin: str) -> int:
    """Note « ce lien ressemble-t-il à une fiche plutôt qu'à une rubrique ? ».

    Le tri compte autant que le filtre : les rubriques apparaissent souvent en haut de
    page (menus), donc sans tri le plafond se consomme entièrement dessus avant
    d'atteindre la moindre fiche. Vu sur un site de prestige où les six premiers liens
    récoltés étaient des menus, pour zéro bien trouvé.
    """
    note = 0
    if _CODE_POSTAL_RE.search(chemin):
        note += 3          # « /fr-lorient-56100/ » : signature d'une fiche localisée
    if re.search(r"[-/](?:ofr-)?\d{4,}(?:[-.]|$)", chemin):
        note += 3          # référence longue en fin de chemin
    note += min(chemin.count("-"), 6)   # slug descriptif = fiche ; slug court = rubrique
    note += min(chemin.strip("/").count("/"), 3)
    return note


def harvest_detail_links(html: str, base_url: str, max_links: int = 40) -> list[str]:
    """Liens de fiches candidates sur une page de liste : même hôte, mots d'annonce,
    et un identifiant numérique (ce qui écarte les pages de rubrique).

    Volontairement permissif — une fiche sans JSON-LD exploitable sera écartée plus loin,
    alors qu'un lien manqué est un bien perdu — mais TRIÉ : les liens qui ressemblent le
    plus à des fiches passent devant, pour que le plafond serve à des biens et non à des
    menus.
    """
    hote = (urlparse(base_url).hostname or "").removeprefix("www.")
    vus: dict[str, int] = {}
    for href in _A_HREF.findall(html or ""):
        url = urljoin(base_url, href)
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            continue
        if (p.hostname or "").removeprefix("www.") != hote:
            continue
        chemin = p.path
        if not _DETAIL_MOTS.search(chemin) or _DETAIL_EXCLU.search(chemin):
            continue
        if not re.search(r"\d{2,}", chemin):  # une fiche porte une référence
            continue
        if url.rstrip("/") == base_url.rstrip("/"):
            continue
        vus.setdefault(url, _ressemblance_fiche(chemin))
    classes = sorted(vus, key=lambda u: -vus[u])
    return classes[:max_links]


# --- Pagination des pages de liste -------------------------------------------- #
# Une seule page de liste était lue par agence, sans jamais paginer. Le plafond ne se
# voyait pas : Orpi Ain Agences avait exactement 40 biens en base — la valeur du cap de
# `harvest_detail_links`, pas son stock — et Diois Immobilier 10 quand son site en
# annonce 45. Un site qui ne rend que sa première page ressemble à un petit site.
#
# On ne code rien par agence : la page suivante se reconnaît à ce qu'elle est LA MÊME URL
# à un nombre près. On remplace donc les suites de chiffres par un joker et on compare les
# gabarits — « /vente/1 » et « /vente/2 » ont le même, « /vente/1 » et « /agence/3 » non.
_CHIFFRES = re.compile(r"\d+")
_REL_NEXT = re.compile(
    r'<(?:a|link)\b[^>]*\brel=["\']?next["\']?[^>]*\bhref=["\']([^"\'#]+)["\']'
    r'|<(?:a|link)\b[^>]*\bhref=["\']([^"\'#]+)["\'][^>]*\brel=["\']?next["\']?',
    re.I,
)
# Un lien de page ne doit pas être confondu avec une fiche : « /annonce/1234 » a le même
# gabarit que « /annonce/1235 ». On exige donc que le nombre reste petit.
_PAGE_MAX = 60


def _gabarit(url: str) -> tuple[str, str, tuple[int, ...]]:
    """(chemin, requête) avec les nombres remplacés par un joker, + les nombres."""
    p = urlparse(url)
    nombres = tuple(int(n) for n in _CHIFFRES.findall(p.path + "?" + (p.query or "")))
    return (_CHIFFRES.sub("#", p.path), _CHIFFRES.sub("#", p.query or ""), nombres)


def pagination_links(html: str, base_url: str, max_pages: int = 8) -> list[str]:
    """Pages de liste suivantes, dans l'ordre. Vide si le site ne pagine pas.

    Trois signaux, du plus fiable au moins : `rel="next"`, puis les liens dont le gabarit
    est identique à celui de la page courante avec un nombre plus grand, puis — quand la
    page courante ne porte aucun nombre — ceux qui ajoutent un simple paramètre de page.
    """
    hote = (urlparse(base_url).hostname or "").removeprefix("www.")
    chemin_ref, query_ref, nombres_ref = _gabarit(base_url)

    candidats: dict[str, tuple[int, ...]] = {}
    for m in _REL_NEXT.finditer(html or ""):
        href = m.group(1) or m.group(2)
        if href:
            candidats[urljoin(base_url, href)] = ()

    for href in _A_HREF.findall(html or ""):
        url = urljoin(base_url, href)
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            continue
        if (p.hostname or "").removeprefix("www.") != hote:
            continue
        if url.rstrip("/") == base_url.rstrip("/"):
            continue
        chemin, query, nombres = _gabarit(url)
        if chemin != chemin_ref:
            continue
        if nombres_ref:
            # Même gabarit, mêmes nombres sauf un, plus grand : c'est une page.
            if query != query_ref or len(nombres) != len(nombres_ref):
                continue
            diff = [i for i, (a, b) in enumerate(zip(nombres_ref, nombres)) if a != b]
            if len(diff) != 1 or nombres[diff[0]] <= nombres_ref[diff[0]]:
                continue
            if nombres[diff[0]] > _PAGE_MAX:
                continue
        else:
            # La page courante n'a aucun nombre : seule une requête de pagination compte.
            if not query or query == query_ref or len(nombres) != 1 or nombres[0] > _PAGE_MAX:
                continue
            if not re.search(r"(page|pag|p|start|offset|debut)=", p.query or "", re.I):
                continue
        candidats[url] = nombres

    return sorted(candidats, key=lambda u: (candidats[u] or (0,)))[:max_pages]
