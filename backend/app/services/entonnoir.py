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
def _charger_cache(chemin: str) -> dict:
    try:
        with open(chemin, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _ecrire_cache(chemin: str, cache: dict) -> None:
    try:
        tmp = chemin + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(tmp, chemin)  # écriture atomique : un run interrompu ne laisse
    except Exception:            # pas un cache tronqué
        pass


def _charger_cache_commune() -> dict:
    return _charger_cache(_COMMUNE_MER_CACHE)


def _ecrire_cache_commune(cache: dict) -> None:
    _ecrire_cache(_COMMUNE_MER_CACHE, cache)


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


# L'eau du set ne se réduit pas à la mer : `bord_eau` (rivière, étang, lac, ria, aber,
# estuaire) est un critère à part entière, pondéré 4. Un bien de l'intérieur posé au bord
# d'une rivière est donc légitime, et la distance à la mer ne le dit pas.
_SIGNAUX_EAU = ("bord_eau", "bord_de_mer")


def _annonce_parle_d_eau(item) -> bool:
    from .export_static import _detect_equipements

    return any(s in _detect_equipements(getattr(item, "description", None))
               for s in _SIGNAUX_EAU)


def filtrer_par_commune(items: list, max_km: float = 10.0,
                        repecher_bord_eau: bool = True) -> tuple[list, list]:
    """Sépare (retenus, écartés) selon la distance à la mer de leur commune.

    Deux garde-fous, parce qu'un entonnoir qui perd une pépite coûte plus qu'il ne
    rapporte :

    - un bien dont la commune n'a pas pu être mesurée est RETENU (mieux vaut enrichir
      pour rien qu'écarter sur une mesure manquante) ;
    - un bien dont l'annonce parle de bord d'eau est RETENU même en commune lointaine —
      la distance à la mer ne mesure pas les rivières, et le set les note.
    """
    par_commune = distance_mer_commune(items)
    maxm = max_km * 1000
    retenus, ecartes, repeches = [], [], 0
    for it in items:
        d = par_commune.get(getattr(it, "code_commune", None))
        if d is None or d <= maxm:
            retenus.append(it)
        elif repecher_bord_eau and _annonce_parle_d_eau(it):
            retenus.append(it)
            repeches += 1
        else:
            ecartes.append(it)
    return retenus, ecartes, repeches


# --------------------------------------------------------------------------- #
# Profil « montagne » (set têtard) — ici l'étage 0 trie vraiment
# --------------------------------------------------------------------------- #
#
# Sur le littoral, l'étage gratuit ne pouvait pas sélectionner : ce qui décidait (distance
# à la mer, proéminence) était mesuré et absent des annonces. Le set têtard est l'inverse.
# Ses critères de tête — budget, capacité d'accueil, prix au m² — sont des champs bruts de
# l'annonce, disponibles avant le moindre appel réseau. L'étage 0 y fait donc le gros du
# tri, et l'étage 1 ne sert qu'à écarter la plaine.
_MONTAGNE_SIGNAUX = {"vue_panoramique": 2.5, "eau": 2.5, "vue": 1.5, "foret": 1.5, "arbore": 1.0}
_MONTAGNE_TRAVAUX = {"habitable": 2.0, "rafraichir": 1.0, "renover": -0.5,
                     "gros_travaux": -3.0, "ruine": -4.0}


def note_annonce_montagne(item, *, prix_max: float = 450_000, chambres_min: int = 3,
                          pm2_bon: float = 1500, pm2_cher: float = 3000) -> float:
    """Note d'annonce du profil montagne. Négative = ne mérite pas l'enrichissement."""
    from .classify import classify
    from .export_static import _detect_pavillon_neuf
    from .quality import classify_quality

    desc = getattr(item, "description", None)
    prix = getattr(item, "prix", None)
    if prix is not None and prix > prix_max:
        return -99.0  # hors budget : rédhibitoire, et gratuit à constater

    note = 0.0

    # Capacité d'accueil. C'est le trou par lequel une maison d'une seule pièce est
    # arrivée deuxième du classement : les chambres manquent une fois sur deux, les
    # pièces presque jamais.
    ch = getattr(item, "nb_chambres", None)
    pieces = getattr(item, "nb_pieces", None)
    if ch is None and pieces:
        ch = max(1, int(pieces) - 1)
    if ch is not None:
        note += 3.0 if ch >= chambres_min else -4.0 * (chambres_min - ch)

    bati = getattr(item, "surface_bati", None)
    if bati:
        note += 2.0 if bati >= 120 else (1.0 if bati >= 90 else -2.0)
        if prix:
            # Rapport qualité/prix approché : le prix au m² brut, faute de référence DVF
            # à ce stade (elle coûte un appel par bien). Les bornes valent pour la zone
            # Drôme/Ardèche/Savoie, dont le secteur médian est à ~2 000 €/m².
            pm2 = prix / bati
            note += 3.0 if pm2 <= pm2_bon else (1.5 if pm2 <= 2200 else (0.0 if pm2 < pm2_cher else -2.0))

    note += _MONTAGNE_TRAVAUX.get(classify(desc).get("condition"), 0.0)

    feats = set(classify_quality(desc).get("features") or [])
    note += sum(w for k, w in _MONTAGNE_SIGNAUX.items() if k in feats)
    if _detect_pavillon_neuf(desc):
        note -= 3.0

    terrain = getattr(item, "surface_terrain", None) or 0
    note += (1.0 if terrain >= 1000 else 0.0) + (1.0 if terrain >= 3000 else 0.0)
    return note


_ALTITUDE_CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data",
                               "commune_altitude_cache.json")


def altitude_commune(items: list) -> dict[str, float]:
    """Altitude par commune, mesurée UNE fois au barycentre de ses annonces (IGN).

    Un appel par commune, pas par bien — c'est ce qui rend l'étage payable. Cache
    permanent : une seconde collecte dans la même région ne le repaie pas.
    """
    from ..enrichment.relief import ReliefProvider

    cache = _charger_cache(_ALTITUDE_CACHE)
    groupes: dict[str, list] = {}
    for it in items:
        code = getattr(it, "code_commune", None)
        if code and getattr(it, "latitude", None) and getattr(it, "longitude", None):
            groupes.setdefault(code, []).append(it)

    provider, neufs = ReliefProvider(), 0
    for code, membres in groupes.items():
        if code in cache:
            continue
        lat = sum(m.latitude for m in membres) / len(membres)
        lon = sum(m.longitude for m in membres) / len(membres)
        try:
            alt = (provider.enrich(lat, lon) or {}).get("altitude")
        except Exception:  # noqa: BLE001
            alt = None
        if alt is None:
            continue  # échec réseau : on ne cache pas, et le bien passe (bénéfice du doute)
        cache[code] = alt
        neufs += 1
        if neufs % 10 == 0:
            _ecrire_cache(_ALTITUDE_CACHE, cache)
    if neufs:
        _ecrire_cache(_ALTITUDE_CACHE, cache)
    return {c: cache[c] for c in groupes if c in cache}


# Ce qui repêche un bien de plaine : l'annonce parle d'un cadre qui vaut le déplacement.
# L'altitude ne mesure ni une rivière ni une vue sur la vallée.
_SIGNAUX_REPECHE = ("eau", "vue_panoramique", "foret")


def _annonce_parle_de_nature(item) -> bool:
    from .quality import classify_quality

    feats = set(classify_quality(getattr(item, "description", None)).get("features") or [])
    return any(s in feats for s in _SIGNAUX_REPECHE)


def filtrer_par_altitude(items: list, min_altitude: float = 250.0,
                         repecher_nature: bool = True) -> tuple[list, list, int]:
    """Sépare (retenus, écartés, repêchés) selon l'altitude de leur commune.

    Mêmes garde-fous que l'étage littoral : une commune non mesurée est RETENUE, et une
    annonce qui parle d'eau, de bois ou de vue dégagée est retenue même en plaine.
    """
    par_commune = altitude_commune(items)
    retenus, ecartes, repeches = [], [], 0
    for it in items:
        alt = par_commune.get(getattr(it, "code_commune", None))
        if alt is None or alt >= min_altitude:
            retenus.append(it)
        elif repecher_nature and _annonce_parle_de_nature(it):
            retenus.append(it)
            repeches += 1
        else:
            ecartes.append(it)
    return retenus, ecartes, repeches


# --------------------------------------------------------------------------- #
# Le pipeline
# --------------------------------------------------------------------------- #
def appliquer(items: list, *, profil: str = "littoral", max_km: float | None = 10.0,
              min_altitude: float | None = 250.0, garder: int | None = None,
              rebut: float = 0.0, log=print) -> list:
    """Passe `items` dans l'entonnoir et renvoie ce qui mérite l'enrichissement.

    - `profil` : « littoral » (étage 1 = distance à la mer) ou « montagne » (étage 1 =
      altitude de la commune). La note d'annonce change avec le profil : les deux sets
      ne cherchent pas la même chose et n'ont pas le même rebut.
    - `max_km` : littoral — écarte les communes plus éloignées de la mer (None = sauté).
    - `min_altitude` : montagne — écarte les communes de plaine (None = étage sauté).
    - `rebut`  : écarte les annonces sous cette note (pavillon neuf, lotissement).
    - `garder` : plafond final, appliqué au classement par note d'annonce (None = tout).
    """
    depart = len(items)
    if not items:
        return items
    montagne = profil == "montagne"
    noter = note_annonce_montagne if montagne else note_annonce

    restants = [it for it in items if noter(it) > rebut] or items
    if len(restants) < depart:
        log(f"  étage 0 (annonce)  : {len(restants)}/{depart} retenus, "
            f"{depart - len(restants)} écartés (rebut évident)")

    if montagne and min_altitude:
        avant = len(restants)
        restants, ecartes, repeches = filtrer_par_altitude(restants, min_altitude)
        if ecartes:
            détail = f", {repeches} repêchés (eau / bois / vue dégagée)" if repeches else ""
            log(f"  étage 1 (commune)  : {len(restants)}/{avant} retenus, "
                f"{len(ecartes)} écartés (commune sous {min_altitude:g} m){détail}")
    elif not montagne and max_km:
        avant = len(restants)
        restants, ecartes, repeches = filtrer_par_commune(restants, max_km)
        if ecartes:
            détail = f", {repeches} repêchés (bord d'eau)" if repeches else ""
            log(f"  étage 1 (commune)  : {len(restants)}/{avant} retenus, "
                f"{len(ecartes)} écartés (commune à plus de {max_km:g} km de la mer){détail}")

    if garder and len(restants) > garder:
        avant = len(restants)
        restants = sorted(restants, key=noter, reverse=True)[:garder]
        log(f"  plafond            : {garder}/{avant} retenus "
            f"(note d'annonce ≥ {noter(restants[-1]):.1f})")

    log(f"  -> {len(restants)} biens à enrichir sur {depart} collectés "
        f"({depart - len(restants)} évités, ~{(depart - len(restants)) * 2.3 / 60:.0f} min économisées)")
    return restants
