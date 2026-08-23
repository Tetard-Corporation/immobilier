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
        return None, "pending", "comparables DVF indisponibles"
    # Bande ±50 % : à ±20 % (ancien barème) presque tous les biens saturaient à 0 ou 1
    # et le sous-pilier ne classait plus rien.
    return _clamp(0.5 - ecart / 100.0), "ok", f"{ecart:+.0f}% vs marché local"


# Niveau de prix ABSOLU, en €/m² : (excellent, cher) pour le terrain nu et pour le bâti.
# Complète « Affaire vs marché », qui est relatif et décrète bonne affaire un bien hors
# de prix dès lors que son secteur est hors de prix. Un terrain au tarif parisien
# (≥ 1 000 €/m²) doit tomber bas quoi qu'en dise le marché local.
_NIVEAU_PRIX_BAREME = {"terrain": (80.0, 400.0), "bati": (1200.0, 3500.0)}
_NIVEAU_PRIX_PLANCHER = 0.25  # note au seuil « cher » ; au-delà, décroissance en 1/prix


def _niveau_prix(flags, ctx):
    prix = ctx.get("prix")
    if not prix:
        return None, "n/a", "prix inconnu"
    if ctx.get("type_bien") == "terrain":
        surface, (bon, cher), unite = ctx.get("surface_terrain"), _NIVEAU_PRIX_BAREME["terrain"], "de terrain"
    else:
        surface, (bon, cher), unite = ctx.get("surface_bati"), _NIVEAU_PRIX_BAREME["bati"], "habitable"
    if not surface:
        return None, "n/a", "surface inconnue"
    ppm = prix / surface
    if ppm <= bon:
        sub = 1.0
    elif ppm >= cher:
        sub = _NIVEAU_PRIX_PLANCHER * cher / ppm
    else:
        sub = _clamp(1 - (ppm - bon) / (cher - bon) * (1 - _NIVEAU_PRIX_PLANCHER))
    return sub, "ok", f"{round(ppm)} €/m² {unite} (repère : {round(bon)}–{round(cher)})"


def _baisse_prix(flags, ctx):
    # Signal informatif seulement quand une baisse est constatée -> on récompense.
    # Sinon on n'a pas d'historique fiable : n/a (exclu) plutôt qu'un 0.5 qui dilue
    # le score de tous les biens vers la moyenne.
    if flags.get("price_decreased"):
        return 1.0, "ok", "prix en baisse constatée"
    return None, "n/a", "pas de baisse constatée"


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


# Poids d'impact par type de risque pour un logement. Les aléas quasi ubiquitaires en
# France et à faible enjeu direct (séisme zone faible, radon) pèsent peu ; les aléas
# potentiellement destructeurs / climatiques (inondation, submersion, recul du trait de
# côte, mouvement de terrain, feu de forêt…) pèsent fort. Corrige l'ancien score qui se
# contentait de COMPTER les risques (tout bien français écopait de ~0.1).
_RISK_WEIGHT = {
    "inondation": 1.0, "risqueCotier": 1.0, "submersionMarine": 1.0, "reculTraitCote": 1.0,
    "mouvementTerrain": 0.9, "ruptureBarrage": 0.9, "avalanche": 0.9, "feuForet": 0.85,
    "nucleaire": 0.8, "canalisationsMatieresDangereuses": 0.6, "retraitGonflementArgile": 0.6,
    "remonteeNappe": 0.5, "pollutionSols": 0.5, "icpe": 0.5, "risqueMinier": 0.5,
    "seisme": 0.2, "radon": 0.15,
}
_RISK_DEFAULT_W = 0.5


def _risques_naturels(flags, ctx):
    r = flags.get("risques")
    if r is None:
        return None, "pending", "Géorisques (enrich)"
    if not r:
        return 1.0, "ok", "aucun risque recensé"
    niveaux = flags.get("risques_niveaux") or {}  # {nom: sévérité [0,1]} si dispo (enrich récent)
    # Pénalité = somme (poids type × sévérité). Sévérité par défaut 0.6 si non renseignée.
    penalty = sum(_RISK_WEIGHT.get(nom, _RISK_DEFAULT_W) * float(niveaux.get(nom, 0.6)) for nom in r)
    sub = _clamp(1 - 0.28 * penalty)
    # Détail lisible : les risques les plus pénalisants d'abord.
    tops = sorted(r, key=lambda n: _RISK_WEIGHT.get(n, _RISK_DEFAULT_W) * float(niveaux.get(n, 0.6)), reverse=True)
    return sub, "ok", ", ".join(tops[:4]) + (f" (+{len(r)-4})" if len(r) > 4 else "")


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
    # Accès porte-à-porte depuis Paris (TGV vers le meilleur hub + voiture) : réaliste,
    # remplace l'ancienne estimation à vol d'oiseau (rail_time_min) jugée fausse.
    lat, lon = ctx.get("latitude"), ctx.get("longitude")
    if lat is None or lon is None:
        return None, "pending", "géoloc manquante"
    from .geo import porte_a_porte_min
    m = porte_a_porte_min(lat, lon)
    if m is None:
        return None, "pending", "trajet indéterminé"
    h, mm = divmod(m, 60)
    return _clamp(1 - (m - 90) / (300 - 90)), "ok", f"~{h}h{mm:02d} porte-à-porte (Paris)"


def _gare(flags, ctx):
    lat, lon = ctx.get("latitude"), ctx.get("longitude")
    if lat is None or lon is None:
        return None, "pending", "géoloc manquante"
    from .gares import nearest_gare
    res = nearest_gare(lat, lon)
    if res is None:
        return None, "pending", "données gares indispo"
    nom, d = res
    return _clamp(1 - d / 30), "ok", f"gare de {nom} à {d} km"


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
    ("prix", "Prix & opportunité", 0.30, [
        ("niveau_prix", "Niveau de prix (€/m²)", 0.45, _niveau_prix),
        ("affaire", "Affaire vs marché", 0.35, _affaire),
        ("baisse_prix", "Négociation (baisse)", 0.20, _baisse_prix),
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
    ("etat", "État & travaux", 0.07, [
        ("travaux", "Niveau de travaux", 1.0, _travaux),
    ]),
    ("accessibilite", "Accessibilité & services", 0.08, [
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
