"""Connecteur SeLoger (groupe Aviv).

⚠️ Protégé par Datadome : sans proxy résidentiel NI cookie `datadome` valide, tout
appel renvoie 403 (cf. docs/OPERATIONS.md §2). D'où le garde-fou `available`.

Refonte 2026 du portail : l'ancien point d'entrée `/list.htm` renvoie désormais un
**404 sec** et les annonces ne sont plus décrites en JSON-LD. La page de résultats
est `/classified-search`, rendue côté serveur ; les cartes d'annonces y portent des
attributs `data-testid` stables (les classes CSS, elles, sont des hachés Emotion
volatils — ne jamais s'appuyer dessus).

Deux particularités du portail conditionnent la construction des requêtes :

- `locations` n'accepte QUE des identifiants internes AVIV (`AD08FR<n>` pour une
  commune) : ni code postal ni code INSEE. On les résout via les URL SEO de commune
  (`/immobilier/achat/immo-<slug>-<dept>/`), qui exposent le `placeId` dans leur
  HTML, puis on met la correspondance en cache disque (`data/seloger_places.json`).
- vocabulaires utiles : `estateTypes` = House | Apartment | Plot | Building | Parking
  (« Land »/« Terrain » sont invalides et font répondre 500) ; `projectTypes` =
  Resale | New_Build | Projected | Life_Annuity.

Les cartes n'exposent PAS de coordonnées : comme la source « agences », la
géolocalisation se fait à la commune via la BAN (cf. `services.geo.geocode_locality`),
côté collecte.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata

import httpx

from ..schemas import SearchCriteria
from ..services.enrich import annotate
from ..services.filters import matches
from .base import NormalizedListing, SearchResult
from .scraper import ScraperBlocked, ScraperSource

# Vocabulaire app -> `estateTypes` SeLoger.
_ESTATE_TYPE = {"maison": "House", "appartement": "Apartment", "terrain": "Plot",
                "immeuble": "Building", "parking": "Parking"}
# Inverse : déduit du libellé de la carte (« Maison à vendre », « Terrain à vendre »…).
# Ordre significatif — « Terrain avec maison neuve » doit tomber sur « terrain ».
_LABEL_TO_APP = (
    ("terrain", "terrain"), ("appartement", "appartement"), ("maison", "maison"),
    ("immeuble", "immeuble"), ("parking", "parking"), ("garage", "parking"),
    ("loft", "appartement"), ("villa", "maison"), ("propriété", "maison"),
    ("château", "maison"), ("ferme", "maison"), ("chalet", "maison"),
    ("longère", "maison"), ("moulin", "maison"), ("hôtel particulier", "maison"),
    ("mas", "maison"), ("bastide", "maison"), ("manoir", "maison"), ("duplex", "appartement"),
    ("studio", "appartement"), ("hôtel", "immeuble"),
)

_SERP_PATH = "/classified-search"
_SEO_PATH = "/immobilier/achat/immo-{slug}-{dep}/"
_PLACES_CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "seloger_places.json")

# Le portail écrit ses nombres avec espace fine insécable (U+202F) comme séparateur
# de milliers et espace insécable (U+00A0) avant l'unité.
_SPACES = "   "
_CARD_SEL = 'div[data-testid="serp-core-classified-card-testid"]'
_PLACE_RE = re.compile(r"locations%253D(AD\d\dFR\d+)|locations=(AD\d\dFR\d+)")
_CARD_ID_RE = re.compile(r'id="classified-card-([A-Z0-9]+)"')


def _fr_num(text: str | None) -> float | None:
    """« 367,22 m² » -> 367.22 ; « 319 000 € » -> 319000.0."""
    if not text:
        return None
    cleaned = re.sub(f"[{_SPACES}]", "", text).replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", cleaned)
    return float(m.group()) if m else None


def _slug(name: str) -> str:
    """« Plœmeur » -> « ploemeur » ; « Clohars-Carnoët » -> « clohars-carnoet »."""
    s = name.replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae").replace("Æ", "AE")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()


def _type_from_label(title: str | None) -> str | None:
    """Type de bien depuis le titre de la carte.

    On ne regarde que le segment AVANT « à vendre » (« Maison à vendre - neuf - … »,
    « Terrain avec maison neuve à vendre - … ») : le reste du titre mentionne
    « … m² de terrain », ce qui ferait passer toute maison pour un terrain."""
    low = (title or "").lower().split(" à vendre")[0]
    for needle, app_type in _LABEL_TO_APP:
        if needle in low:
            return app_type
    return None


class SeLogerSource(ScraperSource):
    name = "seloger"
    label = "SeLoger"
    base_url = "https://www.seloger.com"

    @property
    def available(self) -> bool:
        # Comme Leboncoin : Datadome bloque les IP datacenter. Sans proxy résidentiel
        # NI cookie Datadome, tout appel renvoie 403. On se déclare indisponible tant
        # qu'aucun des deux n'est configuré, pour ne pas gaspiller d'appels.
        return bool(self._settings.proxy_url or self._settings.seloger_datadome)

    def _headers(self) -> dict:
        """En-têtes d'un vrai navigateur + cookie Datadome récolté depuis Chrome.

        Datadome recoupe le cookie avec l'empreinte de la requête : l'UA doit être
        celui du navigateur qui a généré le cookie (cf. scripts/datadome_cookies.py,
        qui écrit SCRAPER_USER_AGENT à côté du cookie).
        """
        h = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        }
        ua = os.environ.get("SCRAPER_USER_AGENT") or self._settings.scraper_user_agent
        if ua:
            h["User-Agent"] = ua
        if self._settings.seloger_datadome:
            h["Cookie"] = f"datadome={self._settings.seloger_datadome}"
        return h

    # -- Résolution des placeIds ------------------------------------------- #
    @staticmethod
    def _load_places() -> dict:
        try:
            with open(_PLACES_CACHE, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:  # noqa: BLE001 - cache absent/corrompu : on repart de zéro
            return {}

    @staticmethod
    def _save_places(places: dict) -> None:
        try:
            os.makedirs(os.path.dirname(_PLACES_CACHE), exist_ok=True)
            with open(_PLACES_CACHE, "w", encoding="utf-8") as fh:
                json.dump(places, fh, ensure_ascii=False, indent=0, sort_keys=True)
        except Exception:  # noqa: BLE001 - cache best-effort
            pass

    def place_id(self, commune: str, departement: str) -> str | None:
        """`placeId` AVIV d'une commune, via son URL SEO. Mis en cache sur disque.

        Le cache mémorise aussi les échecs (valeur `None`) : une commune dont l'URL
        SEO n'existe pas ne doit pas être redemandée à chaque collecte."""
        dep = str(departement)[:3].zfill(2)
        key = f"{_slug(commune)}-{dep}"
        places = self._load_places()
        if key in places:
            return places[key]
        path = _SEO_PATH.format(slug=_slug(commune), dep=dep)
        found = None
        try:
            html = self._get(path, headers=self._headers()).text
            m = _PLACE_RE.search(html)
            found = (m.group(1) or m.group(2)) if m else None
        except (ScraperBlocked, httpx.HTTPError):
            return None  # échec transitoire : ne PAS mémoriser
        places[key] = found
        self._save_places(places)
        return found

    # -- Construction de la requête ---------------------------------------- #
    def _params(self, c: SearchCriteria, place_ids: list[str], page: int = 1) -> dict:
        property_types = c.property_types or ["terrain", "maison", "appartement"]
        codes = sorted({_ESTATE_TYPE[t] for t in property_types if t in _ESTATE_TYPE})
        params = {
            "distributionTypes": "Buy",
            "estateTypes": ",".join(codes),
            # Resale seul : les programmes neufs (New_Build/Projected) sont des
            # annonces de promoteur, hors périmètre (et souvent hors budget).
            "projectTypes": "Resale",
            "locations": ",".join(place_ids),
        }
        # Seules les bornes de PRIX sont filtrées côté serveur : `surfaceMin` et
        # `landSurfaceMin` sont acceptés puis ignorés par le portail (vérifié en live),
        # les surfaces sont donc filtrées côté client via services.filters.matches.
        if c.prix_max is not None:
            params["priceMax"] = str(int(c.prix_max))
        if c.prix_min is not None:
            params["priceMin"] = str(int(c.prix_min))
        if page > 1:
            params["page"] = str(page)
        return params

    # -- Parsing ------------------------------------------------------------ #
    @staticmethod
    def _text(card, testid: str) -> str | None:
        el = card.css_first(f'[data-testid="{testid}"]')
        if el is None:
            return None
        return el.text(separator=" ", strip=True) or None

    @classmethod
    def _parse_card(cls, card) -> NormalizedListing | None:
        card_id = (card.attributes.get("id") or "").replace("classified-card-", "")
        link = card.css_first('a[data-testid="card-mfe-covering-link-testid"]')
        if not card_id or link is None:
            return None
        title = link.attributes.get("title") or ""
        url = link.attributes.get("href")

        # « 4 pièces · 2 chambres · 85,73 m² · 367,22 m² de terrain »
        facts = cls._text(card, "cardmfe-keyfacts-testid") or ""
        pieces = re.search(r"(\d+)\s*pièce", facts)
        chambres = re.search(r"(\d+)\s*chambre", facts)
        terrain = re.search(r"([\d" + _SPACES + r",]+)\s*m²\s*de\s*terrain", facts)
        # La surface bâtie est le « m² » qui n'est PAS suivi de « de terrain ».
        bati = None
        for m in re.finditer(r"([\d" + _SPACES + r",]+)\s*m²(?!\s*de\s*terrain)", facts):
            bati = _fr_num(m.group(1))
            break

        # « 319 000 €   3 721 €/m² » : le premier montant est le prix de vente.
        prix = _fr_num((cls._text(card, "cardmfe-price-testid") or "").split("€")[0])
        if prix is None:
            prix = _fr_num(title.split("-")[2]) if title.count("-") >= 2 else None

        # « Keraude-Breuzent-Kerabus, Ploemeur (56270) »
        addr = cls._text(card, "cardmfe-description-box-address") or ""
        cp = re.search(r"\((\d{5})\)", addr)
        commune = None
        m_com = re.search(r"([^,()]+)\s*\(\d{5}\)", addr)
        if m_com:
            commune = m_com.group(1).strip()
        type_bien = _type_from_label(title)

        return NormalizedListing(
            source="seloger",
            external_id=card_id,
            type_bien=type_bien,
            prix=prix,
            surface_terrain=_fr_num(terrain.group(1)) if terrain else None,
            surface_bati=bati,
            nb_pieces=int(pieces.group(1)) if pieces else None,
            nb_chambres=int(chambres.group(1)) if chambres else None,
            adresse=addr or title or None,
            commune=commune,
            code_postal=cp.group(1) if cp else None,
            departement=(cp.group(1)[:2] if cp else None),
            dpe_classe=(cls._text(card, "card-mfe-energy-performance-class") or "").strip().lower() or None,
            url=url,
            description=cls._text(card, "cardmfe-description-text-test-id"),
            flags={},
            raw={"title": title, "keyfacts": facts, "address": addr},
        )

    @classmethod
    def _parse(cls, html: str) -> list[NormalizedListing]:
        """Cartes d'annonces de la SERP. Repli regex si selectolax est absent."""
        try:
            from selectolax.parser import HTMLParser
        except ImportError:  # pragma: no cover - dépendance optionnelle
            return []
        out = []
        for card in HTMLParser(html).css(_CARD_SEL):
            try:
                item = cls._parse_card(card)
            except Exception:  # noqa: BLE001 - une carte malformée ne casse pas la page
                item = None
            if item is not None:
                out.append(item)
        return out

    @staticmethod
    def total_count(html: str) -> int | None:
        """Total annoncé par la page, quand elle le dit (best-effort).

        La SERP `/classified-search` ne l'affiche pas toujours : la pagination
        s'arrête donc sur une page vide, pas sur ce compteur."""
        for pattern in (r"Découvrez ([\d" + _SPACES + r"]+) annonces",
                        r'"offerCount":\s*([\d]+)',
                        r'"results":\s*([\d]+)'):
            m = re.search(pattern, html)
            if m:
                return int(re.sub(f"[{_SPACES}]", "", m.group(1)))
        return None

    # -- API source --------------------------------------------------------- #
    def search_place(self, criteria: SearchCriteria, place_ids: list[str],
                     page: int = 1) -> list[NormalizedListing]:
        """Une page de résultats pour des `placeId` donnés (sans filtrage client)."""
        params = self._params(criteria, place_ids, page)
        html = self._get(_SERP_PATH, params=params, headers=self._headers()).text
        return [annotate(it) for it in self._parse(html)]

    def search(self, criteria: SearchCriteria) -> SearchResult:
        """Recherche sur la commune des critères (code postal ou nom de commune).

        SeLoger n'accepte pas de code postal : on résout d'abord le `placeId` de la
        commune. Sans commune identifiable, on ne peut rien demander -> résultat vide
        plutôt qu'un appel gaspillé."""
        place_ids = self._place_ids_for(criteria)
        if not place_ids:
            return SearchResult(items=[], total=0, curseur_suivant=None, credits_estimes=0)
        items = self.search_place(criteria, place_ids, max(criteria.page, 1))
        filtered = [it for it in items if matches(it, criteria)]
        return SearchResult(items=filtered, total=None, curseur_suivant=None, credits_estimes=0)

    def _place_ids_for(self, criteria: SearchCriteria) -> list[str]:
        """Résout la localisation des critères en `placeId`(s) SeLoger."""
        from ..services.geo import geocode_locality

        label = criteria.secteur or criteria.code_postal or criteria.adresse
        if not label:
            return []
        geo = geocode_locality(label)
        if not geo or not geo.get("nom"):
            return []
        pid = self.place_id(geo["nom"], geo.get("departement") or (criteria.code_postal or "")[:2])
        return [pid] if pid else []

    def get(self, external_id: str, bases: list[str] | None = None) -> NormalizedListing | None:
        # Pas de fiche unitaire exploitable sans traverser Datadome une fois de plus :
        # la carte de la SERP porte déjà tout ce que le pipeline consomme.
        return None
