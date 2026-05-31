"""Connecteur de l'API officielle Pappers Immobilier.

Doc : https://immobilier.pappers.fr/api/documentation
Base : https://api-immobilier.pappers.fr/v1/  — auth via header `api-key`.

Données = parcelles cadastrales + bases associées (ventes/DVF, DPE, permis...).
Le parsing est défensif : la spec ne fige pas tous les champs et l'on ne veut pas
casser si la forme évolue.
"""

from __future__ import annotations

import time

import httpx

from ..config import Settings, get_settings
from ..schemas import SearchCriteria
from .base import ListingSource, NormalizedListing, SearchResult

# Coût en crédits par base (cf. doc). 1 crédit de base pour la parcelle elle-même.
_BASE_COST = {
    "ventes": 2,
    "proprietaires": 2,
    "occupants": 2,
    "permis": 1,
    "fonds_de_commerce": 1,
    "batiments": 1,
    "dpe": 1,
    "coproprietes": 1,
    "amenagements": 1,
    "documents_urbanisme": 1,
}

# Correspondance critères normalisés -> paramètres de l'API Pappers.
_PARAM_MAP = {
    "code_postal": "code_postal",
    "code_commune": "code_commune",
    "departement": "departement",
    "region": "region",
    "adresse": "adresse",
    "latitude": "latitude",
    "longitude": "longitude",
    "distance": "distance",
    "prix_min": "prix_vente_min",
    "prix_max": "prix_vente_max",
    "surface_terrain_min": "surface_terrain_vente_min",
    "surface_terrain_max": "surface_terrain_vente_max",
    "surface_bati_min": "surface_bati_vente_min",
    "surface_bati_max": "surface_bati_vente_max",
    "nb_pieces_min": "nombre_pieces_vente_min",
    "nb_pieces_max": "nombre_pieces_vente_max",
    "date_vente_min": "date_vente_min",
    "date_vente_max": "date_vente_max",
    "annee_construction_min": "annee_construction_batiment_min",
    "annee_construction_max": "annee_construction_batiment_max",
}


def criteria_to_params(c: SearchCriteria, default_bases: list[str]) -> dict:
    """Traduit des critères normalisés en query params Pappers."""

    params: dict[str, object] = {}
    for field, target in _PARAM_MAP.items():
        value = getattr(c, field)
        if value is not None:
            params[target] = value

    if c.types_local:
        params["type_local_vente"] = ",".join(c.types_local)
    if c.natures_vente:
        params["nature_vente"] = ",".join(c.natures_vente)
    if c.dpe_classes:
        params["classe_bilan_dpe"] = ",".join(c.dpe_classes)

    bases = c.bases if c.bases is not None else default_bases
    if bases:
        params["bases"] = ",".join(bases)

    # Champs additionnels utiles : adresse complète (région/dpt) + bounding_box (géoloc).
    params["champs_supplementaires"] = "adresse,bounding_box"

    if c.curseur:
        params["curseur"] = c.curseur
    else:
        params["page"] = c.page
    params["par_page"] = c.par_page
    return params


def estimate_credits(bases: list[str], nb_results: int) -> int:
    per_parcelle = 1 + sum(_BASE_COST.get(b, 1) for b in bases)
    return per_parcelle * max(nb_results, 0)


def _latlng_from_parcelle(p: dict) -> tuple[float | None, float | None]:
    top_left = p.get("top_left") or {}
    br = p.get("bottom_right") or {}
    lat1 = top_left.get("lat")
    lon1 = top_left.get("lon")
    lat2 = br.get("latitude")
    lon2 = br.get("longitude")
    lats = [v for v in (lat1, lat2) if isinstance(v, (int, float))]
    lons = [v for v in (lon1, lon2) if isinstance(v, (int, float))]
    if lats and lons:
        return sum(lats) / len(lats), sum(lons) / len(lons)
    return None, None


def _latest_vente(ventes: list[dict]) -> dict | None:
    valid = [v for v in ventes if isinstance(v, dict)]
    if not valid:
        return None
    return max(valid, key=lambda v: v.get("date") or "")


def parse_parcelle(p: dict) -> NormalizedListing:
    """Convertit une parcelle Pappers (ParcelleFiche) en annonce normalisée."""

    ventes = p.get("ventes") or []
    vente = _latest_vente(ventes)
    dpe_list = p.get("dpe") or []
    dpe_classe = None
    if dpe_list and isinstance(dpe_list[0], dict):
        dpe_classe = dpe_list[0].get("classe_bilan_dpe") or dpe_list[0].get("classe_consommation")

    codes_postaux = p.get("codes_postaux") or []
    code_postal = codes_postaux[0] if codes_postaux else None
    lat, lng = _latlng_from_parcelle(p)

    type_bien = None
    prix = surface_bati = surface_terrain = nb_pieces = None
    date_mutation = None
    if vente:
        prix = vente.get("valeur_fonciere")
        surface_bati = vente.get("surface_reelle_bati")
        surface_terrain = vente.get("surface_terrain")
        nb_pieces = vente.get("nombre_pieces")
        date_mutation = vente.get("date")
        type_bien = vente.get("type_local")
        nature = vente.get("nature") or ""
        if not type_bien:
            if "terrain" in nature or (surface_bati in (None, 0)):
                type_bien = "terrain"

    if surface_terrain in (None, 0):
        surface_terrain = p.get("contenance")

    numero = p.get("numero") or ""
    return NormalizedListing(
        source="pappers",
        external_id=numero,
        type_bien=type_bien,
        prix=prix,
        surface_terrain=surface_terrain,
        surface_bati=surface_bati,
        nb_pieces=nb_pieces,
        adresse=p.get("adresse"),
        commune=p.get("commune"),
        code_postal=code_postal,
        code_commune=p.get("code_commune"),
        departement=p.get("departement"),
        latitude=lat,
        longitude=lng,
        parcelle=numero,
        date_mutation=date_mutation,
        dpe_classe=dpe_classe,
        url=f"https://immobilier.pappers.fr/carte?parcelle={numero}" if numero else None,
        raw=p,
    )


def _extract_list(data) -> tuple[list[dict], int | None, str | None]:
    """Extrait (parcelles, total, curseurSuivant) d'une réponse, de forme variable."""

    if isinstance(data, list):
        return data, len(data), None
    if isinstance(data, dict):
        for key in ("resultats", "parcelles", "data"):
            if isinstance(data.get(key), list):
                return (
                    data[key],
                    data.get("total"),
                    data.get("curseurSuivant") or data.get("curseur_suivant"),
                )
        # Réponse "fiche" unique.
        if data.get("numero"):
            return [data], 1, None
    return [], 0, None


class PappersSource(ListingSource):
    name = "pappers"
    label = "Pappers Immobilier"

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._cache: dict[str, tuple[float, object]] = {}

    @property
    def available(self) -> bool:
        return self._settings.pappers_configured

    # -- HTTP ---------------------------------------------------------------- #
    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._settings.pappers_base_url,
                headers={"api-key": self._settings.pappers_api_key},
                timeout=self._settings.http_timeout_seconds,
            )
        return self._client

    def _request(self, path: str, params: dict):
        cache_key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < self._settings.cache_ttl_seconds:
            return cached[1]
        resp = self._get_client().get(path, params=params)
        resp.raise_for_status()
        data = resp.json()
        self._cache[cache_key] = (now, data)
        return data

    # -- API ----------------------------------------------------------------- #
    def search(self, criteria: SearchCriteria) -> SearchResult:
        if not self.available:
            raise RuntimeError("Clé API Pappers non configurée (PAPPERS_API_KEY).")
        bases = criteria.bases if criteria.bases is not None else self._settings.default_bases_list
        params = criteria_to_params(criteria, self._settings.default_bases_list)
        data = self._request("/parcelles", params)
        parcelles, total, curseur = _extract_list(data)
        items = [parse_parcelle(p) for p in parcelles]
        return SearchResult(
            items=items,
            total=total,
            curseur_suivant=curseur,
            credits_estimes=estimate_credits(bases, len(items)),
        )

    def get(self, external_id: str, bases: list[str] | None = None) -> NormalizedListing | None:
        if not self.available:
            raise RuntimeError("Clé API Pappers non configurée (PAPPERS_API_KEY).")
        params: dict[str, object] = {"champs_supplementaires": "adresse,bounding_box"}
        chosen = bases if bases is not None else self._settings.default_bases_list
        if chosen:
            params["bases"] = ",".join(chosen)
        data = self._request(f"/parcelles/{external_id}", params)
        parcelles, _, _ = _extract_list(data)
        return parse_parcelle(parcelles[0]) if parcelles else None
