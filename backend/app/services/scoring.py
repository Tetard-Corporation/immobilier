"""Score d'investissement (0–100) explicable et tolérant aux données partielles.

Chaque composante produit un sous-score normalisé [0,1] et un poids. Les composantes
dont la donnée est absente sont ignorées et leur poids redistribué, si bien que le
score fonctionne dès aujourd'hui (état, nature, nuisances, baisse de prix) et
s'enrichit automatiquement quand le Lot A fournira `constructible`, `est_zone_au`,
`risques`, `peb_zone`, `ecart_prix_pct`.
"""

from __future__ import annotations

from dataclasses import dataclass

# Poids par défaut des composantes (échelle relative ; renormalisés sur les présentes).
WEIGHTS = {
    "affaire": 0.30,  # prix vs comparables DVF (Lot A)
    "constructible": 0.18,  # zonage / zone AU (Lot A)
    "nature": 0.18,  # qualité/nature (vue, forêt, eau, exception)
    "etat": 0.12,  # niveau de travaux
    "risques": 0.10,  # Géorisques (Lot A)
    "aerien": 0.06,  # PEB / nuisances aériennes (Lot A)
    "nuisances": 0.06,  # vis-à-vis, route, mitoyenneté
    "prix_baisse": 0.05,  # signal de négociation
}

_ETAT_SCORE = {0: 1.0, 1: 0.85, 2: 0.70, 3: 0.50, 4: 0.35}
_PEB_SCORE = {"A": 0.0, "B": 0.25, "C": 0.5, "D": 0.7}

_LABELS = {
    "affaire": "Bonne affaire (prix vs marché)",
    "constructible": "Constructibilité",
    "nature": "Qualité / nature",
    "etat": "État (travaux)",
    "risques": "Risques",
    "aerien": "Nuisances aériennes (PEB)",
    "nuisances": "Nuisances de proximité",
    "prix_baisse": "Baisse de prix",
}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class ScoreResult:
    score: float
    components: list[dict]

    def as_dict(self) -> dict:
        return {"score": self.score, "components": self.components}


def compute_score(flags: dict, *, has_text: bool) -> ScoreResult:
    """Calcule le score d'investissement à partir des flags d'une annonce."""
    subs: dict[str, float] = {}

    # --- Composantes Lot A (présentes seulement si enrichies) ---
    ecart = flags.get("ecart_prix_pct")
    if ecart is not None:
        # -20 % sous le marché -> 1.0 ; +20 % au-dessus -> 0.0
        subs["affaire"] = _clamp(0.5 - ecart / 40.0)

    if "constructible" in flags:
        if flags.get("est_zone_au"):
            subs["constructible"] = 0.9  # bientôt constructible
        else:
            subs["constructible"] = 1.0 if flags.get("constructible") else 0.15

    risques = flags.get("risques")
    if risques is not None:
        subs["risques"] = _clamp(1.0 - 0.2 * len(risques))

    peb = flags.get("peb_zone")
    if peb is not None:
        subs["aerien"] = _PEB_SCORE.get(str(peb).upper(), 0.0)

    # --- Composantes disponibles dès maintenant ---
    if has_text:
        niveau = flags.get("niveau_travaux")
        subs["etat"] = _ETAT_SCORE.get(niveau, 0.6) if niveau is not None else 0.6

        nature_score = flags.get("nature_score") or 0
        bonus = 0.2 if flags.get("nature_exception") else 0.0
        subs["nature"] = _clamp(0.5 + 0.12 * nature_score + bonus)

        subs["nuisances"] = _clamp(1.0 - 0.25 * len(flags.get("nuisances") or []))

    subs["prix_baisse"] = 1.0 if flags.get("price_decreased") else 0.5

    total_weight = sum(WEIGHTS[k] for k in subs)
    if total_weight == 0:
        return ScoreResult(score=0.0, components=[])

    components = []
    score_acc = 0.0
    for key, sub in subs.items():
        weight = WEIGHTS[key] / total_weight
        contribution = round(weight * sub * 100, 1)
        score_acc += weight * sub
        components.append(
            {
                "key": key,
                "label": _LABELS[key],
                "subscore": round(sub, 3),
                "weight": round(weight, 3),
                "contribution": contribution,
            }
        )

    components.sort(key=lambda c: c["contribution"], reverse=True)
    return ScoreResult(score=round(score_acc * 100, 1), components=components)
