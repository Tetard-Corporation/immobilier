"""Connecteur immobilier.notaires.fr via son API JSON publique.

Endpoint public (pas d'anti-bot) : `/pub-services/inotr-www-annonces/v1/annonces`.
Le filtre `departement` est appliqué côté serveur ; le type de bien et la transaction
(VENTE vs LOCATION) ne sont pas filtrables -> on filtre côté client. Inventaire distinct
(ventes notariales, successions, biens ruraux souvent à rénover et absents des portails).
"""

from __future__ import annotations

from ..schemas import SearchCriteria
from ..services.enrich import annotate
from ..services.filters import matches
from .base import NormalizedListing, SearchResult
from .scraper import ScraperSource

_PATH = "/pub-services/inotr-www-annonces/v1/annonces"

# Codes typeBien notaires -> vocabulaire app.
_TYPE = {
    "MAI": "maison", "APP": "appartement", "TER": "terrain",
    "IMM": "immeuble", "LOC": "local_commercial", "PAR": "parking",
}


def _num(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


class NotairesSource(ScraperSource):
    name = "notaires"
    label = "Notaires de France"
    base_url = "https://www.immobilier.notaires.fr"

    @staticmethod
    def _normalize(a: dict) -> NormalizedListing:
        url = a.get("urlDetailAnnonceFr") or ""
        if url and url.startswith("/"):
            url = "https://www.immobilier.notaires.fr" + url
        return NormalizedListing(
            source="notaires",
            external_id=str(a.get("annonceId") or a.get("id") or ""),
            type_bien=_TYPE.get(a.get("typeBien")),
            prix=_num(a.get("prixTotal") or a.get("prixAffiche")),
            surface_terrain=_num(a.get("surfaceTerrain")),
            surface_bati=_num(a.get("surface")),
            nb_pieces=int(a["nbPieces"]) if a.get("nbPieces") else None,
            nb_chambres=int(a["nbChambres"]) if a.get("nbChambres") else None,
            commune=a.get("communeNom"),
            code_postal=a.get("codePostal"),
            code_commune=a.get("inseeCommune"),
            departement=a.get("inseeDepartement"),
            url=url or None,
            description=a.get("descriptionFr"),
            flags={},
            raw=a,
        )

    def search(self, criteria: SearchCriteria) -> SearchResult:
        params = {
            "parPage": min(max(criteria.par_page, 1), 100),
            "page": max(criteria.page - 1, 0),
        }
        if criteria.departement:
            params["departement"] = criteria.departement
        resp = self._get(_PATH, params=params, headers={"Accept": "application/json"})
        data = resp.json()
        ads = data.get("annonceResumeDto") or []
        items = []
        for ad in ads:
            # On exclut les locations (l'API ne filtre pas la transaction)…
            if ad.get("typeTransaction") and ad["typeTransaction"] != "VENTE":
                continue
            # …les biens hors ligne, et les biens explicitement vendus/retirés
            # (bienVendu/bienRetire valent "INCONNU" par défaut -> truthy, donc on
            # compare explicitement à OUI/True).
            if ad.get("statut") and ad["statut"] != "LIGNE":
                continue
            if ad.get("bienVendu") in ("OUI", True) or ad.get("bienRetire") in ("OUI", True):
                continue
            items.append(annotate(self._normalize(ad)))
        filtered = [it for it in items if matches(it, criteria)]
        return SearchResult(items=filtered, total=data.get("nbTotalAnnonces"),
                            curseur_suivant=None, credits_estimes=0)

    def get(self, external_id: str, bases: list[str] | None = None) -> NormalizedListing | None:
        return None
