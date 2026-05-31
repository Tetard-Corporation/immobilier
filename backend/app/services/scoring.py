"""Score d'investissement hiérarchique : piliers thématiques → sous-piliers.

Architecture en deux niveaux, explicable et tolérante aux données partielles :

  Score global (0–100)
    └── Piliers (Prix, Foncier, Cadre, Risques, État, Accessibilité)
          └── Sous-piliers (ex. Prix → Affaire vs marché, Négociation)

Chaque sous-pilier produit un sous-score [0,1] + un statut (`ok` / `pending` /
`n/a`). Le score d'un pilier = moyenne pondérée de ses sous-piliers *disponibles*
(poids redistribué) ; le score global = moyenne pondérée des piliers *disponibles*.
Un sous-pilier `pending` (donnée d'enrichissement pas encore branchée, ex. PEB,
trajet train) n'écrase rien : il est listé mais exclu du calcul.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Évaluateurs de sous-piliers : (flags, ctx) -> (subscore|None, status, detail)
# status ∈ {"ok", "pending", "n/a"}
# --------------------------------------------------------------------------- #
_ETAT = {"habitable": 1.0, "rafraichir": 0.85, "renover": 0.7, "gros_travaux": 0.5, "ruine": 0.35}
_ZONE = {"U": 1.0, "AU": 0.9, "A": 0.15, "N": 0.1}
_PEB = {"A": 0.0, "B": 0.25, "C": 0.5, "D": 0.7}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _affaire(flags, ctx):
    ecart = flags.get("ecart_prix_pct")
    if ecart is None:
        return None, "pending", "comparables DVF requis (clé Pappers)"
    return _clamp(0.5 - ecart / 40.0), "ok", f"{ecart:+.0f}% vs marché"


def _baisse_prix(flags, ctx):
    dec = bool(flags.get("price_decreased"))
    return (1.0 if dec else 0.5), "ok", "en baisse" if dec else "prix stable"


def _zonage(flags, ctx):
    z = flags.get("zone_urba")
    if z is None:
        return None, "pending", "zonage GPU (enrich)"
    return _ZONE.get(z, 0.5), "ok", f"zone {z}" + (" — bientôt constructible" if z == "AU" else "")


def _terrain(flags, ctx):
    st = ctx.get("surface_terrain")
    if not st:
        return None, "n/a", "surface terrain inconnue"
    return _clamp(0.4 + min(st, 2000) / 2000 * 0.6), "ok", f"{int(st)} m²"


def _nature(flags, ctx):
    if not ctx.get("has_text"):
        return None, "n/a", "pas de description"
    return _clamp(0.5 + 0.12 * (flags.get("nature_score") or 0)), "ok", f"score nature {flags.get('nature_score') or 0}"


def _exception(flags, ctx):
    if not ctx.get("has_text"):
        return None, "n/a", "pas de description"
    return (1.0 if flags.get("nature_exception") else 0.4), "ok", "exception" if flags.get("nature_exception") else "ordinaire"


def _authenticite(flags, ctx):
    if not ctx.get("has_text"):
        return None, "n/a", "pas de description"
    has = "authentique" in (flags.get("features") or [])
    return (1.0 if has else 0.4), "ok", "cachet/authentique" if has else "non précisé"


def _risques_naturels(flags, ctx):
    r = flags.get("risques")
    if r is None:
        return None, "pending", "Géorisques (enrich)"
    return _clamp(1 - 0.15 * len(r)), "ok", f"{len(r)} risque(s)" if r else "aucun risque"


def _nuisances_proximite(flags, ctx):
    if not ctx.get("has_text"):
        return None, "n/a", "pas de description"
    n = flags.get("nuisances") or []
    return _clamp(1 - 0.25 * len(n)), "ok", ", ".join(n) if n else "aucune nuisance signalée"


def _pollution_eau(flags, ctx):
    s = flags.get("pollution_eau_score")
    if s is None:
        return None, "pending", "qualité eau Hub'Eau (enrich)"
    pol = flags.get("pollutions") or []
    detail = "eau conforme" if flags.get("eau_potable_conforme") else "eau NON conforme"
    if pol:
        detail += " — " + ", ".join(pol)
    return s, "ok", detail


def _aerien(flags, ctx):
    peb = flags.get("peb_zone")
    if peb is None:
        return None, "pending", "PEB / servitudes aéro"
    return _PEB.get(str(peb).upper(), 0.0), "ok", f"PEB {peb}"


def _travaux(flags, ctx):
    cond = flags.get("condition")
    if cond is None:
        return None, "n/a", "état inconnu"
    return _ETAT.get(cond, 0.6), "ok", cond


def _train(flags, ctx):
    t = flags.get("rail_time_min")
    if t is None:
        return None, "pending", "trajet train (clé Navitia)"
    return _clamp(1 - t / 180), "ok", f"{t} min en train"


def _gare(flags, ctx):
    d = flags.get("dist_gare_km")
    if d is None:
        return None, "pending", "proximité gare (enrich)"
    return _clamp(1 - d / 15), "ok", f"gare à {d} km"


def _fibre(flags, ctx):
    f = flags.get("fibre")
    if f is None:
        return None, "pending", "éligibilité fibre (Arcep)"
    return (1.0 if f else 0.0), "ok", "fibre" if f else "pas de fibre"


# --------------------------------------------------------------------------- #
# Définition des piliers (poids relatifs, renormalisés sur les disponibles)
# (clé, libellé, poids, [(clé_sous, libellé_sous, poids_sous, évaluateur), ...])
# --------------------------------------------------------------------------- #
PILLARS = [
    ("prix", "Prix & opportunité", 0.25, [
        ("affaire", "Affaire vs marché", 0.6, _affaire),
        ("baisse_prix", "Négociation (baisse)", 0.4, _baisse_prix),
    ]),
    ("foncier", "Foncier & constructibilité", 0.20, [
        ("zonage", "Zonage / constructibilité", 0.6, _zonage),
        ("terrain", "Présence de terrain", 0.4, _terrain),
    ]),
    ("cadre", "Cadre & nature", 0.20, [
        ("nature", "Qualité nature", 0.5, _nature),
        ("exception", "Caractère d'exception", 0.3, _exception),
        ("authenticite", "Authenticité / cachet", 0.2, _authenticite),
    ]),
    ("risques", "Risques & nuisances", 0.15, [
        ("risques_naturels", "Risques naturels/techno", 0.30, _risques_naturels),
        ("pollution_eau", "Pollution / qualité de l'eau", 0.30, _pollution_eau),
        ("nuisances_proximite", "Nuisances de proximité", 0.25, _nuisances_proximite),
        ("aerien", "Nuisances aériennes (PEB)", 0.15, _aerien),
    ]),
    ("etat", "État & travaux", 0.10, [
        ("travaux", "Niveau de travaux", 1.0, _travaux),
    ]),
    ("accessibilite", "Accessibilité & services", 0.10, [
        ("train", "Trajet train", 0.5, _train),
        ("gare", "Proximité gare", 0.25, _gare),
        ("fibre", "Fibre", 0.25, _fibre),
    ]),
]


@dataclass
class ScoreResult:
    score: float
    pillars: list[dict]


def compute_score(flags: dict, ctx: dict | None = None) -> ScoreResult:
    """Calcule le score global (0–100) et le détail piliers → sous-piliers."""
    ctx = ctx or {}
    pillars_out: list[dict] = []
    global_acc = 0.0
    global_w = 0.0

    for pkey, plabel, pweight, subs in PILLARS:
        sub_out = []
        sub_acc = 0.0
        sub_w = 0.0
        for skey, slabel, sweight, evaluator in subs:
            subscore, status, detail = evaluator(flags, ctx)
            entry = {
                "key": skey,
                "label": slabel,
                "weight_raw": sweight,
                "status": status,
                "detail": detail,
                "subscore": round(subscore, 3) if subscore is not None else None,
            }
            if status == "ok" and subscore is not None:
                sub_acc += sweight * subscore
                sub_w += sweight
            sub_out.append(entry)

        pillar_score = round(sub_acc / sub_w * 100, 1) if sub_w else None
        # Normalise les poids des sous-piliers disponibles (pour l'affichage).
        for e in sub_out:
            if e["status"] == "ok" and sub_w:
                e["weight"] = round(e["weight_raw"] / sub_w, 3)
                e["contribution"] = round(e["weight"] * e["subscore"] * 100, 1)
            else:
                e["weight"] = 0.0
                e["contribution"] = 0.0
            del e["weight_raw"]

        pillar = {
            "key": pkey,
            "label": plabel,
            "weight_raw": pweight,
            "score": pillar_score,
            "status": "ok" if pillar_score is not None else "pending",
            "subpillars": sub_out,
        }
        if pillar_score is not None:
            global_acc += pweight * (pillar_score / 100)
            global_w += pweight
        pillars_out.append(pillar)

    # Normalise les poids des piliers disponibles + contribution au score global.
    for p in pillars_out:
        if p["score"] is not None and global_w:
            p["weight"] = round(p["weight_raw"] / global_w, 3)
            p["contribution"] = round(p["weight"] * (p["score"] / 100) * 100, 1)
        else:
            p["weight"] = 0.0
            p["contribution"] = 0.0
        del p["weight_raw"]

    score = round(global_acc / global_w * 100, 1) if global_w else 0.0
    pillars_out.sort(key=lambda p: p["contribution"], reverse=True)
    return ScoreResult(score=score, pillars=pillars_out)


def scoring_schema() -> dict:
    """Structure des piliers/sous-piliers + poids (pour le front)."""
    return {
        "pillars": [
            {
                "key": pkey,
                "label": plabel,
                "weight": pweight,
                "subpillars": [{"key": skey, "label": slabel, "weight": sweight} for skey, slabel, sweight, _ in subs],
            }
            for pkey, plabel, pweight, subs in PILLARS
        ]
    }
