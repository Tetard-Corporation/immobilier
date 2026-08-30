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
import time

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
# « ensoleille » (plein sud, exposition sud, très lumineux) rejoint les signaux : la
# mesure réelle du soleil d'hiver coûte 4 requêtes IGN et n'arrive qu'au réchauffage,
# bien après cet étage. Ce que l'annonce en dit vaut donc mieux que rien pour décider
# QUI mérite d'être mesuré — sans jamais valoir la mesure elle-même (poids modeste).
_MONTAGNE_SIGNAUX = {"vue_panoramique": 2.5, "eau": 2.5, "vue": 1.5, "foret": 1.5,
                     "arbore": 1.0, "ensoleille": 1.5}
# « À rénover » n'est plus un défaut : le groupe accepte un peu de travaux, et ne refuse
# que la rénovation complète (gros_travaux) et la ruine.
_MONTAGNE_TRAVAUX = {"habitable": 2.0, "rafraichir": 1.5, "renover": 0.5,
                     "gros_travaux": -3.0, "ruine": -4.0}


def note_annonce_montagne(item, *, prix_max: float = 250_000, chambres_min: int = 3,
                          chambres_max: int = 4, reference_m2: float | None = None) -> float:
    """Note d'annonce du profil montagne. Négative = ne mérite pas l'enrichissement.

    `reference_m2` : prix au m² du secteur (DVF). Sans lui, le terme prix est NEUTRE.

    Le prix au m² ne veut rien dire dans l'absolu, et s'en servir comme tel contredit le
    critère qu'on prétend approcher. Mesuré : une coupe au prix absolu écartait
    Saint-François-Longchamp à 2 694 €/m² — « cher » — alors que le secteur y est à
    3 700 et que le score en fait une pépite à 78,6. Faute de référence, on ne tranche pas.
    """
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
        # Le plafond compte autant que le plancher depuis que le groupe a tranché
        # « 3/4 chambres MAX » : une maison de sept chambres n'est pas une maison de
        # retrait, et il y en avait une parmi les pépites publiées.
        if ch > chambres_max:
            note -= 2.0 * (ch - chambres_max)

    bati = getattr(item, "surface_bati", None)
    if bati:
        # Bande, et non « plus c'est grand mieux c'est » : au-delà de 230 m² on chauffe,
        # on entretient et on rénove une maison qui n'a pas été demandée.
        note += (-2.0 if bati < 90 else 2.0 if bati <= 180 else 0.5 if bati <= 230 else -2.0)
        if prix and reference_m2:
            # Rapport qualité/prix : le RATIO au marché local, comme le fait le critère
            # `rapport_qualite_prix` du set. Bornes calées sur la distribution mesurée
            # (p20 = 0,75 · médiane = 1,17 · p80 = 1,68).
            ratio = (prix / bati) / reference_m2
            note += 3.0 if ratio <= 0.75 else (1.5 if ratio <= 1.17 else (0.0 if ratio < 1.7 else -2.0))

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
        # Réessais : l'API IGN plafonne les rafales. Sans back-off, un lot de 300 communes
        # se fait jeter après la vingtaine et l'étage devient un no-op — que le garde-fou
        # « commune non mesurée = retenue » fait passer pour un run propre. Vécu : 25
        # communes mesurées sur plusieurs centaines, 3 biens écartés, aucun message.
        alt = None
        for essai in range(3):
            try:
                alt = (provider.enrich(lat, lon) or {}).get("altitude")
            except Exception:  # noqa: BLE001
                alt = None
            if alt is not None:
                break
            time.sleep(1.5 * (essai + 1))
        if alt is None:
            continue  # échec réseau : on ne cache pas, et le bien passe (bénéfice du doute)
        cache[code] = alt
        neufs += 1
        if neufs % 10 == 0:
            _ecrire_cache(_ALTITUDE_CACHE, cache)
    if neufs:
        _ecrire_cache(_ALTITUDE_CACHE, cache)
    return {c: cache[c] for c in groupes if c in cache}


_DVF_CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data",
                          "commune_dvf_cache.json")


def prix_m2_commune(items: list, log=print) -> dict[str, float]:
    """Prix au m² bâti du secteur, par commune (DVF), mesuré une fois au barycentre.

    C'est l'étage qui décide vraiment pour ce set : le critère de tête est le rapport
    qualité/prix, et il n'a de sens que rapporté au marché local. 0,3 à 0,8 s par commune,
    cache permanent — une seconde collecte dans la même région ne le repaie pas.
    """
    from ..enrichment.dvf import DvfComparablesProvider

    cache = _charger_cache(_DVF_CACHE)
    groupes: dict[str, list] = {}
    for it in items:
        code = getattr(it, "code_commune", None)
        if code and getattr(it, "latitude", None) and getattr(it, "longitude", None):
            groupes.setdefault(code, []).append(it)

    provider, neufs, sans = DvfComparablesProvider(), 0, 0
    for code, membres in groupes.items():
        if code in cache:
            continue
        lat = sum(m.latitude for m in membres) / len(membres)
        lon = sum(m.longitude for m in membres) / len(membres)
        try:
            ref = (provider.enrich(lat, lon) or {}).get("prix_m2_secteur_bati")
        except Exception:  # noqa: BLE001
            ref = None
        if ref is None:
            sans += 1
            continue  # commune sans ventes exploitables : pas de référence, pas de verdict
        cache[code] = ref
        neufs += 1
        if neufs % 25 == 0:
            _ecrire_cache(_DVF_CACHE, cache)
    if neufs:
        _ecrire_cache(_DVF_CACHE, cache)
    mesures = {c: cache[c] for c in groupes if c in cache}
    log(f"  références DVF     : {len(mesures)}/{len(groupes)} communes ({neufs} neuves, "
        f"{sans} sans ventes exploitables)")
    return mesures


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
    communes = {getattr(it, "code_commune", None) for it in items} - {None}
    non_mesurees = len(communes - set(par_commune))
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
    return retenus, ecartes, repeches, non_mesurees


# --------------------------------------------------------------------------- #
# Le pipeline
# --------------------------------------------------------------------------- #
def appliquer(items: list, *, profil: str = "littoral", max_km: float | None = 10.0,
              min_altitude: float | None = 250.0, garder: int | None = None,
              prix_max: float | None = None, rebut: float = 0.0, log=print) -> list:
    """Passe `items` dans l'entonnoir et renvoie ce qui mérite l'enrichissement.

    - `profil` : « littoral » (étage 1 = distance à la mer) ou « montagne » (étage 1 =
      référence de prix DVF par commune, puis altitude). La note d'annonce change avec le
      profil : les deux sets ne cherchent pas la même chose et n'ont pas le même rebut.
    - `max_km` : littoral — écarte les communes plus éloignées de la mer (None = sauté).
    - `min_altitude` : montagne — écarte les communes de plaine (None = étage sauté).
    - `prix_max` : montagne — plafond budgétaire, écarté sans le moindre appel réseau.
    - `rebut`  : écarte les annonces sous cette note (pavillon neuf, lotissement).
    - `garder` : plafond final, appliqué au classement par note d'annonce (None = tout).
    """
    depart = len(items)
    if not items:
        return items
    montagne = profil == "montagne"
    plafond = {"prix_max": prix_max} if (montagne and prix_max) else {}
    noter = (lambda it: note_annonce_montagne(it, **plafond)) if montagne else note_annonce

    restants = [it for it in items if noter(it) > rebut] or items
    if len(restants) < depart:
        log(f"  étage 0 (annonce)  : {len(restants)}/{depart} retenus, "
            f"{depart - len(restants)} écartés (rebut évident)")

    if montagne:
        # Étage 1 — le marché local. C'est LUI qui classe : le critère de tête du set est
        # le rapport qualité/prix, et un prix au m² ne veut rien dire hors de son secteur.
        refs = prix_m2_commune(restants, log=log)
        noter = lambda it: note_annonce_montagne(  # noqa: E731
            it, reference_m2=refs.get(getattr(it, "code_commune", None)), **plafond)
        if min_altitude:
            avant = len(restants)
            restants, ecartes, repeches, non_mesurees = filtrer_par_altitude(restants, min_altitude)
            détail = f", {repeches} repêchés (eau / bois / vue dégagée)" if repeches else ""
            log(f"  étage 1 (altitude) : {len(restants)}/{avant} retenus, "
                f"{len(ecartes)} écartés (commune sous {min_altitude:g} m){détail}")
            if non_mesurees:
                # Un étage qui échoue en silence ressemble trait pour trait à un étage qui
                # ne trouve rien : il faut que le run le dise.
                log(f"  ⚠ {non_mesurees} communes non mesurées (IGN) — leurs biens sont "
                    f"retenus par défaut, l'étage n'a donc pas tranché pour eux")
    elif max_km:
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
