"""Entonnoir de collecte : filtrer avant d'enrichir, du moins cher au plus cher.

Le problème qu'il résout. L'enrichissement coûte ~2,3 s par bien et la mesure fine
(distance à la mer au point près) 4 appels IGN de plus. Sur la dernière collecte, 929
annonces ont été enrichies pour une vingtaine de pépites : l'essentiel du temps est parti
dans des biens que le score écartait ensuite.

Le principe. Quatre étages, chacun plus cher et plus précis que le précédent, et à chaque
passage on ne garde que les survivants :

    0. annonce   — texte, prix, surfaces           0 appel réseau
    1. commune   — distance à la mer du chef-lieu  1 appel IGN par COMMUNE
    2. point     — enrichissement complet          ~2,3 s par bien
    3. fin       — mesures au point près           ~4 appels IGN par bien

L'étage 1 est celui qui change l'économie du pipeline : la distance à la mer coûte le même
appel pour toutes les annonces d'une commune. Trente appels suffisent là où le calcul au
point près en demanderait plus de mille, et il trie sur le critère qui décide vraiment
plutôt que sur le vocabulaire de l'annonce.

Pourquoi ne pas tout trier sur le texte. Mesuré sur 690 biens dont les pépites étaient
connues : garder les 60 meilleures annonces au texte n'en conservait que 7 sur 18. Les
critères qui décident — distance à la mer, proéminence du relief — sont mesurés et
n'apparaissent jamais dans l'annonce. L'étage 0 sert donc à écarter le rebut évident
(pavillon neuf, lotissement viabilisé), pas à choisir les pépites.
"""

from __future__ import annotations

import json
import os

# Signaux lisibles dans l'annonce, pondérés comme dans les sets littoraux.
_SIGNAUX = {"bord_de_mer": 5.0, "bord_eau": 4.0, "vue": 4.0, "en_hauteur": 2.0}

_COMMUNE_MER_CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data",
                                  "commune_mer_cache.json")


# --------------------------------------------------------------------------- #
# Étage 0 — l'annonce (gratuit)
# --------------------------------------------------------------------------- #
def note_annonce(item) -> float:
    """Note d'annonce, sans enrichissement ni réseau. Négative = rebut probable."""
    from .export_static import _detect_equipements, _detect_pavillon_neuf

    feats = set(_detect_equipements(getattr(item, "description", None)))
    note = sum(w for k, w in _SIGNAUX.items() if k in feats)

    if _detect_pavillon_neuf(getattr(item, "description", None)):
        note -= 4.0  # pavillon neuf / lotissement : l'inverse du bien recherché

    prix, terrain = getattr(item, "prix", None), getattr(item, "surface_terrain", None)
    if terrain and prix:
        ppm = prix / terrain
        note += 3.0 if ppm <= 60 else 1.5 if ppm <= 150 else 0.0
    if (getattr(item, "type_bien", None) or "").lower() == "terrain":
        note += 1.0
    if (terrain or 0) >= 1500:
        note += 1.0
    return note


# --------------------------------------------------------------------------- #
# Étage 1 — la commune (1 appel par commune, partagé par ses annonces)
# --------------------------------------------------------------------------- #
def _charger_cache_commune() -> dict:
    try:
        with open(_COMMUNE_MER_CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _ecrire_cache_commune(cache: dict) -> None:
    try:
        tmp = _COMMUNE_MER_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(tmp, _COMMUNE_MER_CACHE)  # écriture atomique : un run interrompu
    except Exception:                        # ne laisse pas un cache tronqué
        pass


def distance_mer_commune(items: list, maxm: int = 12000, step: int = 2000) -> dict[str, float]:
    """Distance à la mer par commune, mesurée UNE fois au barycentre de ses annonces.

    Renvoie {code_commune: distance_m}. La grille est VOLONTAIREMENT grossière — pas de
    résolution sous 2 km, rien au-delà de 12 km — parce qu'à cet étage la question est
    binaire : arrière-pays ou littoral. Le classement fin du littoral est le travail de
    l'étage 3, au point près. Cette grille coûte 2 appels IGN par commune ; en descendre
    la maille à 1 km en coûterait 7, pour une réponse identique au seuil de 10 km.
    """
    from .export_static import _query_sea_distance

    cache = _charger_cache_commune()
    groupes: dict[str, list] = {}
    for it in items:
        code = getattr(it, "code_commune", None)
        if code and getattr(it, "latitude", None) and getattr(it, "longitude", None):
            groupes.setdefault(code, []).append(it)

    neufs = 0
    for code, membres in groupes.items():
        if code in cache:
            continue
        lat = sum(m.latitude for m in membres) / len(membres)
        lon = sum(m.longitude for m in membres) / len(membres)
        res = _query_sea_distance(lat, lon, maxm=maxm, step=step)
        if res is None:
            continue  # échec réseau : on ne cache pas, et le bien passe (bénéfice du doute)
        cache[code] = res.get("dist_mer_m")
        neufs += 1
        if neufs % 10 == 0:
            _ecrire_cache_commune(cache)
    if neufs:
        _ecrire_cache_commune(cache)
    return {c: cache[c] for c in groupes if c in cache}


def filtrer_par_commune(items: list, max_km: float = 10.0) -> tuple[list, list]:
    """Sépare (retenus, écartés) selon la distance à la mer de leur commune.

    Un bien dont la commune n'a pas pu être mesurée est RETENU : mieux vaut enrichir pour
    rien qu'écarter une pépite sur une mesure manquante.
    """
    par_commune = distance_mer_commune(items)
    maxm = max_km * 1000
    retenus, ecartes = [], []
    for it in items:
        d = par_commune.get(getattr(it, "code_commune", None))
        (ecartes if (d is not None and d > maxm) else retenus).append(it)
    return retenus, ecartes


# --------------------------------------------------------------------------- #
# Le pipeline
# --------------------------------------------------------------------------- #
def appliquer(items: list, *, max_km: float | None = 10.0, garder: int | None = None,
              rebut: float = 0.0, log=print) -> list:
    """Passe `items` dans l'entonnoir et renvoie ce qui mérite l'enrichissement.

    - `max_km` : écarte les communes plus éloignées de la mer (None = étage sauté).
    - `rebut`  : écarte les annonces sous cette note (pavillon neuf, lotissement).
    - `garder` : plafond final, appliqué au classement par note d'annonce (None = tout).
    """
    depart = len(items)
    if not items:
        return items

    restants = [it for it in items if note_annonce(it) > rebut] or items
    if len(restants) < depart:
        log(f"  étage 0 (annonce)  : {len(restants)}/{depart} retenus, "
            f"{depart - len(restants)} écartés (rebut évident)")

    if max_km is not None:
        avant = len(restants)
        restants, ecartes = filtrer_par_commune(restants, max_km)
        if ecartes:
            log(f"  étage 1 (commune)  : {len(restants)}/{avant} retenus, "
                f"{len(ecartes)} écartés (commune à plus de {max_km:g} km de la mer)")

    if garder and len(restants) > garder:
        avant = len(restants)
        restants = sorted(restants, key=note_annonce, reverse=True)[:garder]
        log(f"  plafond            : {garder}/{avant} retenus "
            f"(note d'annonce ≥ {note_annonce(restants[-1]):.1f})")

    log(f"  -> {len(restants)} biens à enrichir sur {depart} collectés "
        f"({depart - len(restants)} évités, ~{(depart - len(restants)) * 2.3 / 60:.0f} min économisées)")
    return restants
