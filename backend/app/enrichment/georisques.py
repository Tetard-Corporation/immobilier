"""Provider Géorisques : risques naturels/technologiques d'un point (sans clé).

On distingue le statut au niveau ADRESSE (prioritaire) du statut communal : un risque
« non connu / inconnu » à l'adresse n'est pas retenu (il fausse le scoring, cf. quasi
toutes les communes ont séisme/radon/argile au niveau commune). On extrait aussi une
sévérité [0,1] à partir du libellé ("faible", "fort"…).
"""

from __future__ import annotations

from .base import EnrichmentProvider


def _verdict(label: str | None) -> bool | None:
    """True = risque avéré, False = absent/non connu, None = pas d'info."""
    s = (label or "").lower()
    if not s:
        return None
    if any(k in s for k in ("non connu", "inconnu", "absent", "pas de risque", "aucun")):
        return False
    if any(k in s for k in ("existant", "faible", "moyen", "modere", "modéré", "fort", "élevé", "eleve")):
        return True
    return None


def _severity(label: str | None) -> float:
    s = (label or "").lower()
    if "très fort" in s or "tres fort" in s or "élevé" in s or "eleve" in s:
        return 1.0
    if "fort" in s:
        return 0.85
    if "moyen" in s or "modéré" in s or "modere" in s:
        return 0.5
    if "faible" in s:
        return 0.25
    return 0.6  # "existant" non qualifié


class GeorisquesProvider(EnrichmentProvider):
    name = "georisques"

    def _fetch(self, lat: float, lon: float) -> dict:
        resp = self._get_client().get(
            f"{self._settings.georisques_api_url}/resultats_rapport_risque",
            params={"latlon": f"{lon},{lat}"},
        )
        resp.raise_for_status()
        data = resp.json()
        risques: list[str] = []
        niveaux: dict[str, float] = {}
        for famille in ("risquesNaturels", "risquesTechnologiques"):
            bloc = data.get(famille) or {}
            if not isinstance(bloc, dict):
                continue
            for nom, val in bloc.items():
                if not (isinstance(val, dict) and val.get("present")):
                    continue
                adr = val.get("libelleStatutAdresse")
                comm = val.get("libelleStatutCommune")
                # Adresse prioritaire ; repli commune si l'adresse ne tranche pas.
                v = _verdict(adr)
                label = adr
                if v is None:
                    v = _verdict(comm)
                    label = comm
                if not v:
                    continue  # non avéré à l'adresse -> ignoré
                risques.append(nom)
                niveaux[nom] = _severity(label)
        return {"risques": risques, "risques_niveaux": niveaux}
