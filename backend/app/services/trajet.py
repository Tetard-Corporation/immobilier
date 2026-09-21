"""Accès depuis Paris : train MESURÉ jusqu'à la gare, route MESURÉE jusqu'au bien.

Pourquoi ce module. Les deux critères d'accès du set étaient calculés à vol d'oiseau, et
le groupe l'a vu avant nous — six commentaires sur quarante et un portent là-dessus :
« beaucoup trop inaccessible ! 1 h de route depuis la gare de Grenoble » (Rencurel),
« besoin d'une voiture, à 45 min de Grenoble » (Pierre-Châtel), « il faut une voiture car
à 1 h de Lyon ou Saint-Étienne » (Anneyron). Le moteur, lui, divisait la distance à vol
d'oiseau par 65 km/h : en montagne, la route fait le tour du massif que la ligne droite
traverse, et le critère annonçait des trajets que personne ne peut faire.

Ce qui est mesuré ici, et par qui :

- **Paris → gare** : la durée moyenne RÉELLEMENT OBSERVÉE, publiée par la SNCF dans sa
  régularité mensuelle par liaison (jeu `regularite-mensuelle-tgv-aqst`). 45 gares
  desservies directement depuis Paris, moyennées sur les mois disponibles. Voir
  `scripts/build_gares_dataset.py`, qui écrit `data/gares_voyageurs.csv`.
- **Gare → bien** : l'itinéraire routier de l'IGN (`data.geopf.fr/navigation`, moteur
  OSRM sur la BD TOPO), c'est-à-dire la vraie route, ses lacets et ses cols. Sans clé,
  ~0,15 s par appel.

Ce qui n'est PAS mesuré, et qu'on refuse donc d'écrire : le temps d'un trajet avec
correspondance. Une gare TER ne porte pas de temps « depuis Paris » — la SNCF ne publie
pas de durée pour ces liaisons, et en inventer une (« TGV jusqu'au hub + tant de TER »)
reproduirait exactement le défaut qu'on répare. La gare TER proche est donc affichée avec
son temps de VOITURE seul, et le trajet depuis Paris passe par la gare qui, elle, a une
durée mesurée.

Deux gares desservies l'hiver par des TGV directs (Moûtiers, Bourg-Saint-Maurice) ne
figurent pas dans le jeu SNCF des liaisons : elles sont donc classées TER ici. C'est une
sous-estimation de l'offre, jamais une surestimation.
"""

from __future__ import annotations

import csv
import functools
import json
import os
import threading
import time
import urllib.parse
import urllib.request

from .geo import haversine_km

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
_GARES = os.path.join(_DATA, "gares_voyageurs.csv")
CACHE_PATH = os.path.join(_DATA, "trajet_cache.json")

_IGN_URL = "https://data.geopf.fr/navigation/itineraire"
_UA = "immobilier-engine/1.0 (open data IGN)"

# Nombre de gares réellement routées par bien. Le pré-tri à vol d'oiseau sert à choisir
# QUI router, jamais à répondre : c'est la route qui tranche, sur 4 candidats plutôt que
# sur un seul, parce que la gare la plus proche n'est pas toujours la mieux reliée
# (Gresse-en-Vercors est à 27 km de Grenoble à vol d'oiseau et à 55 min par la route).
_N_HUBS = 2
_N_GARES = 1

# Débit maximal vers l'IGN, tous fils confondus. Le service répond 429 au-delà, et un 429
# coûte plus cher qu'une attente : il déclenche une reprise avec temporisation, donc le
# réchauffage ralentit en accélérant. Mesuré : à quatre fils sans limiteur, le catalogue
# était annoncé à 8 heures ; le même travail tient en 1 h 30 à trois appels par seconde.
_DEBIT_MAX_PAR_S = 3.0

# Repli quand la route n'a pas pu être mesurée. Volontairement PESSIMISTE : le défaut
# qu'on répare est un optimisme, et un bien non mesuré ne doit pas monter au classement
# grâce à ça. 45 km/h est la vitesse effective observée (voir docs/OPERATIONS.md) contre
# les 65 km/h que le moteur supposait.
_REPLI_KMH = 45
# Forfait d'accès (sortir du hameau, se garer). Court : à 10 minutes, le repli annonçait
# 12 minutes pour une gare située à 1,4 km, ce qu'un lecteur sait faux — et un chiffre
# visiblement faux décrédibilise ceux qui sont justes.
_REPLI_OVERHEAD_MIN = 4


@functools.lru_cache(maxsize=1)
def _gares(chemin: str = _GARES) -> list[dict]:
    """Toutes les gares voyageurs. `paris_min` non nul = desservie en direct depuis Paris."""
    out: list[dict] = []
    if not os.path.exists(chemin):
        return out
    with open(chemin, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            paris = row.get("paris_min") or ""
            out.append({
                "nom": row["nom"], "lat": lat, "lon": lon,
                "commune": row.get("commune") or "", "departement": row.get("departement") or "",
                "paris_min": int(paris) if paris.strip().isdigit() else None,
                "paris_gare": row.get("paris_gare") or "",
            })
    return out


def gares_chargees() -> int:
    """Nombre de gares dans le référentiel (0 = fichier absent → critère en repli)."""
    return len(_gares())


def _type(gare: dict) -> str:
    return "TGV" if gare["paris_min"] else "TER"


_verrou_debit = threading.Lock()
_dernier_appel = [0.0]


def _attendre_son_tour() -> None:
    """Espace les appels à l'IGN, quel que soit le nombre de fils qui en demandent."""
    intervalle = 1.0 / _DEBIT_MAX_PAR_S
    with _verrou_debit:
        attente = _dernier_appel[0] + intervalle - time.monotonic()
        if attente > 0:
            time.sleep(attente)
        _dernier_appel[0] = time.monotonic()


def route_min(depart: tuple[float, float], arrivee: tuple[float, float],
              *, essais: int = 4) -> tuple[int, float] | None:
    """(minutes, km) par la route entre deux points, mesurés par l'IGN. None si échec.

    Quatre essais avec une attente qui double, parce que le service plafonne les rafales
    et le dit par un refus, pas par une réponse lente : mesuré, un réchauffage à huit
    workers échouait sur 22 % des appels là où quatre workers en perdaient 6 %. Un point
    abandonné n'est PAS mis en cache — il sera redemandé au réchauffage suivant.
    """
    params = urllib.parse.urlencode({
        "resource": "bdtopo-osrm", "profile": "car", "optimization": "fastest",
        "start": f"{depart[1]},{depart[0]}", "end": f"{arrivee[1]},{arrivee[0]}",
        "getSteps": "false", "geometryFormat": "geojson",
    })
    for k in range(essais):
        try:
            _attendre_son_tour()
            req = urllib.request.Request(f"{_IGN_URL}?{params}", headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                d = json.loads(resp.read())
            duree, dist = d.get("duration"), d.get("distance")
            if duree is None or dist is None:
                return None
            return round(duree / 60), round(dist / 1000, 1)
        except Exception:
            if k + 1 < essais:
                time.sleep(1.5 * 2 ** k)
    return None


def _repli_min(km: float) -> int:
    return round(km / _REPLI_KMH * 60 + _REPLI_OVERHEAD_MIN)


def mesurer(lat: float, lon: float) -> dict | None:
    """Mesure l'accès d'un point : gare depuis Paris + gare la plus proche. None si échec.

    Renvoie None plutôt qu'un résultat partiel quand aucune route n'a pu être calculée :
    un échec réseau ne doit pas se figer dans le cache sous les traits d'une mesure.
    """
    gares = _gares()
    if not gares or lat is None or lon is None:
        return None

    hubs = [g for g in gares if g["paris_min"]]
    # Pré-tri sur le total ESTIMÉ (train mesuré + route à vol d'oiseau) et non sur la
    # seule distance : depuis la Savoie, Lyon est plus loin que Chambéry mais son train
    # est une heure plus court, donc les deux doivent être routés.
    candidats = sorted(
        hubs, key=lambda g: g["paris_min"] + _repli_min(haversine_km(lat, lon, g["lat"], g["lon"]))
    )[:_N_HUBS]

    meilleur = None
    for g in candidats:
        route = route_min((g["lat"], g["lon"]), (lat, lon))
        if route is None:
            continue
        minutes, km = route
        total = g["paris_min"] + minutes
        if meilleur is None or total < meilleur["total_min"]:
            meilleur = {"gare": g["nom"], "type": "TGV", "paris_min": g["paris_min"],
                        "paris_gare": g["paris_gare"], "voiture_min": minutes,
                        "voiture_km": km, "total_min": total}
    if meilleur is None:
        return None

    proches = sorted(gares, key=lambda g: haversine_km(lat, lon, g["lat"], g["lon"]))[:_N_GARES]
    proche = None
    for g in proches:
        route = route_min((g["lat"], g["lon"]), (lat, lon))
        if route is None:
            continue
        minutes, km = route
        if proche is None or minutes < proche["voiture_min"]:
            proche = {"gare": g["nom"], "type": _type(g), "voiture_min": minutes, "voiture_km": km}
    if proche and proche["gare"] != meilleur["gare"]:
        meilleur["proche"] = proche
    # La gare la plus proche est parfois le hub lui-même : son temps de voiture est alors
    # déjà celui du trajet principal, et le répéter en « alternative » n'apprend rien.
    acces = (proche or meilleur)["voiture_min"]
    return {"trajet_paris": meilleur, "acces_gare_min": acces,
            # Le temps jusqu'à une gare DESSERVIE DEPUIS PARIS, distinct du précédent :
            # « il faut plus de gares et surtout les TGV ». Un arrêt TER à dix minutes et
            # une gare TGV à dix minutes ne valent pas la même chose, et un critère qui
            # ne regarde que la gare la plus proche ne sait pas les distinguer.
            "acces_gare_tgv_min": meilleur["voiture_min"],
            "porte_a_porte_min": meilleur["total_min"]}


def charger_cache(chemin: str = CACHE_PATH) -> dict:
    try:
        with open(chemin, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _ecrire_cache(cache: dict, chemin: str = CACHE_PATH) -> None:
    try:
        with open(chemin, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
    except Exception:
        pass


def cle(lat: float, lon: float) -> str:
    """Clé de cache : 3 décimales, soit ~100 m — deux biens du même hameau la partagent."""
    return f"{round(lat, 3)},{round(lon, 3)}"


def acces(lat: float, lon: float, cache: dict, *, live: bool = False) -> dict:
    """Drapeaux d'accès d'un bien. Cache-only par défaut (l'export ne mesure jamais).

    Même règle que l'ensoleillement et le tourisme : 7 appels réseau par bien, c'est trop
    pour un catalogue entier à l'export, donc la mesure se fait au réchauffage
    (`scripts/warm_trajet.py`) et l'export relit le cache.
    """
    if lat is None or lon is None:
        return {}
    k = cle(lat, lon)
    if k in cache:
        res = cache[k]
        # Le temps jusqu'à une gare TGV a été ajouté après les premiers réchauffages :
        # on le retrouve dans le trajet déjà mesuré plutôt que de rejeter le cache, qui
        # représente des heures d'appels et n'a rien perdu de sa validité.
        if "acces_gare_tgv_min" not in res and (res.get("trajet_paris") or {}).get("voiture_min"):
            res = dict(res, acces_gare_tgv_min=res["trajet_paris"]["voiture_min"])
        return res
    if not live:
        return {}
    res = mesurer(lat, lon)
    if res is None:
        return {}
    cache[k] = res
    _ecrire_cache(cache)
    return res


def repli_estime(lat: float, lon: float) -> dict:
    """Accès ESTIMÉ (vol d'oiseau, vitesse pessimiste) pour un bien jamais réchauffé.

    Le repli existe parce qu'un critère `pending` est EXCLU du score au lieu de le
    baisser : un bien non mesuré monterait au classement (cf. docs/OPERATIONS.md). Il
    n'est jamais présenté comme une mesure — `estime: True` le suit jusqu'à l'affichage.
    """
    gares = _gares()
    if not gares or lat is None or lon is None:
        return {}
    hubs = [g for g in gares if g["paris_min"]]
    if not hubs:
        return {}
    hub = min(hubs, key=lambda g: g["paris_min"] + _repli_min(haversine_km(lat, lon, g["lat"], g["lon"])))
    km_hub = haversine_km(lat, lon, hub["lat"], hub["lon"])
    voiture = _repli_min(km_hub)
    proche = min(gares, key=lambda g: haversine_km(lat, lon, g["lat"], g["lon"]))
    km_proche = haversine_km(lat, lon, proche["lat"], proche["lon"])
    trajet = {"gare": hub["nom"], "type": "TGV", "paris_min": hub["paris_min"],
              "paris_gare": hub["paris_gare"], "voiture_min": voiture,
              "voiture_km": round(km_hub, 1), "total_min": hub["paris_min"] + voiture,
              "estime": True}
    if proche["nom"] != hub["nom"]:
        trajet["proche"] = {"gare": proche["nom"], "type": _type(proche),
                            "voiture_min": _repli_min(km_proche),
                            "voiture_km": round(km_proche, 1), "estime": True}
    return {"trajet_paris": trajet,
            "acces_gare_min": (trajet.get("proche") or trajet)["voiture_min"],
            "acces_gare_tgv_min": trajet["voiture_min"],
            "porte_a_porte_min": trajet["total_min"], "trajet_estime": True}
