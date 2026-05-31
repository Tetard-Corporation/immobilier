"""Provider pollution : qualité de l'eau potable de la commune (Hub'Eau, sans clé).

Résout la commune par reverse-geocoding (BAN), interroge Hub'Eau (analyses du contrôle
sanitaire de l'eau potable) et en déduit un score de conformité + les familles de
pollution détectées (pesticides, nitrates, PFAS). Le pollution des sols reste, lui,
capturé par Géorisques (`risques`).
"""

from __future__ import annotations

import re

from .base import EnrichmentProvider


def _limit_num(raw: str | None) -> float | None:
    m = re.search(r"([\d.,]+)", raw or "")
    return float(m.group(1).replace(",", ".")) if m else None


def analyse_resultats(rows: list[dict]) -> dict:
    """Agrège les analyses Hub'Eau en score de conformité + familles de pollution."""
    if not rows:
        return {}
    preleve: dict[str, str] = {}
    for x in rows:
        preleve.setdefault(x.get("code_prelevement"), x.get("conclusion_conformite_prelevement") or "")
    total = len(preleve) or 1
    non_conformes = sum(1 for c in preleve.values() if "non conforme" in c.lower() or "non-conforme" in c.lower())
    score = round(1 - non_conformes / total, 3)

    pollutions: list[str] = []
    for x in rows:
        lib = (x.get("libelle_parametre") or "").lower()
        res = x.get("resultat_numerique")
        lim = _limit_num(x.get("limite_qualite_parametre"))
        exceed = isinstance(res, (int, float)) and lim is not None and res > lim
        fam = (
            "pesticides" if "pesti" in lib
            else "nitrates" if "nitrate" in lib
            else "pfas" if ("fluor" in lib or "pfas" in lib or "perfluor" in lib)
            else None
        )
        if fam and exceed and fam not in pollutions:
            pollutions.append(fam)

    return {
        "pollution_eau_score": score,
        "eau_potable_conforme": non_conformes == 0,
        "pollutions": pollutions,
    }


class PollutionProvider(EnrichmentProvider):
    name = "pollution"

    def _reverse_commune(self, lat: float, lon: float) -> str | None:
        resp = self._get_client().get(self._settings.ban_reverse_url, params={"lat": lat, "lon": lon})
        resp.raise_for_status()
        feats = resp.json().get("features") or []
        return feats[0]["properties"].get("citycode") if feats else None

    def _fetch(self, lat: float, lon: float) -> dict:
        code_commune = self._reverse_commune(lat, lon)
        if not code_commune:
            return {}
        resp = self._get_client().get(
            self._settings.hubeau_eau_potable_url,
            params={"code_commune": code_commune, "size": 100, "sort": "desc"},
        )
        if resp.status_code not in (200, 206):
            return {}
        return analyse_resultats(resp.json().get("data") or [])
