"""Export d'un instantané statique (JSON + photos) pour le front GitHub Pages.

GitHub Pages est un hébergement statique : il ne peut ni exécuter le moteur Python ni
scraper. Le front lit donc ce snapshot produit par le backend. On exporte :
- les sets de filtres (têtard + sous-sets) avec leurs préférences,
- le catalogue des biens réels rencontrés (dédoublonnés), avec photos téléchargées,
  le score d'investissement détaillé et le match_score recalculé pour CHAQUE set,
- l'historique systématique des recherches.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:  # optimisation des images (optionnelle : repli sur l'octet brut si absente)
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

from ..models import FilterSet, Listing, SavedListing, SearchHistory
from .criteres import identifiant, registre
from .filtersets import resolve_criteria
from .geo import haversine_km
from .preferences import evaluate
from .scoring import compute_score

# Colonnes DB dont le nom correspond 1:1 aux clés `flags` consommées par evaluate().
# (mapping inverse de search.upsert_listing, qui écrit flags.get(<col>) -> colonne)
_FLAG_COLS = (
    "condition", "niveau_travaux", "features", "nuisances", "nature_score",
    "nature_exception", "score", "score_details", "constructible", "est_zone_au",
    "zone_urba", "altitude", "rail_time_min", "risques", "prix_m2_secteur",
    "ecart_prix_pct", "pollution_eau_score", "eau_potable_conforme", "pollutions",
    "age_median", "part_gauche", "population_commune", "isolement_score", "price_decreased",
)

# Colonnes de flags à PERSISTER dans data.json pour un round-trip seed->export sans
# perte (sinon les biens re-seedés perdent DVF/pollution/GPU/socio -> scoring dégradé).
# On exclut score/score_details (recalculés) et features (fusionnées avec la détection).
_PERSIST_FLAG_COLS = tuple(c for c in _FLAG_COLS if c not in ("score", "score_details", "features"))

_UA = "Mozilla/5.0 (compatible; immobilier-export/1.0)"
# Overpass refuse (406) les User-Agent de la forme "Mozilla/5.0 (compatible; ...)" :
# les requêtes POI/infra échouaient donc en silence (exception avalée -> flags absents).
_UA_OVERPASS = "immobilier-export/1.0"
_MAX_PHOTOS = 12
# Distances aux infrastructures bruyantes (autoroute/voie ferrée) via Overpass (OSM),
# mises en cache sur disque pour ne pas re-interroger à chaque export.
_INFRA_CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "infra_cache.json")
# Instance Overpass par défaut : la FRANÇAISE. Le choix n'est pas un réglage de
# performance mais de CONTENU — voir `verifier_overpass` juste dessous.
_OVERPASS = os.environ.get("OVERPASS_URL", "https://overpass.openstreetmap.fr/api/interpreter")

# Point témoin : un bourg ardéchois dont on sait qu'il a des commerces (48 relevés par une
# instance saine). Sert à démasquer une instance qui répond « rien » au lieu d'échouer.
_TEMOIN = (44.6596, 4.3435)


def verifier_overpass(url: str | None = None) -> tuple[bool, str]:
    """L'instance interrogée couvre-t-elle bien la France ?

    Une instance régionale (overpass.osm.ch, suisse) répond **200 avec zéro élément** sur
    un point français. Rien ne distingue cette réponse de « il n'y a pas de commerce ici » :
    le cache se remplit de zéros, le critère « village vivant » tombe à 0 pour tout un lot,
    et le run se déclare réussi. Vécu : 850 biens neufs tous à zéro commerce, dont des
    bourgs qui ont un supermarché, et un runbook qui recommandait cette instance sur la foi
    d'un taux de succès mesuré... sur le code HTTP.
    """
    global _OVERPASS
    ancien, _OVERPASS = _OVERPASS, (url or _OVERPASS)
    try:
        res = _query_poi(*_TEMOIN)
    finally:
        _OVERPASS = ancien
    if res is None:
        return False, f"{url or ancien} : pas de réponse exploitable"
    n = res.get("n_commerces") or 0
    if n == 0:
        return False, (f"{url or ancien} : zéro commerce sur le point témoin — instance "
                       f"qui ne couvre pas la France, le cache se remplirait de zéros")
    return True, f"{url or ancien} : {n} commerces sur le point témoin, couverture OK"


def _load_infra_cache() -> dict:
    try:
        with open(_INFRA_CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


_POI_CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "poi_cache.json")


def _query_poi(lat: float, lon: float) -> dict | None:
    """Commerces/services de proximité (≤3 km) + remontée de ski la plus proche (≤25 km)."""
    q = (f'[out:json][timeout:30];('
         f'nwr(around:3000,{lat},{lon})[shop~"^(supermarket|bakery|convenience|butcher|greengrocer|grocery)$"];'
         f'nwr(around:3000,{lat},{lon})[amenity~"^(pharmacy|school|doctors|marketplace|post_office|bank)$"];'
         f'way(around:25000,{lat},{lon})[aerialway~"gondola|chair_lift|cable_car|mixed_lift"];);out center 500;')
    try:
        data = urllib.parse.urlencode({"data": q}).encode()
        req = urllib.request.Request(_OVERPASS, data=data, headers={"User-Agent": _UA_OVERPASS})
        with urllib.request.urlopen(req, timeout=45) as r:
            payload = json.loads(r.read())
    except Exception:
        return None
    n_commerces, ski = 0, None
    for el in payload.get("elements", []):
        t = el.get("tags", {})
        if t.get("shop") or t.get("amenity"):
            n_commerces += 1
        elif t.get("aerialway"):
            c = el.get("center") or ({"lat": el.get("lat"), "lon": el.get("lon")} if el.get("lat") else None)
            if c and c.get("lat") is not None:
                dm = round(haversine_km(lat, lon, c["lat"], c["lon"]) * 1000)
                if ski is None or dm < ski:
                    ski = dm
    return {"n_commerces": n_commerces, "dist_ski_m": ski, "ski_checked": True}


# Mode cache-only : n'interroge PAS Overpass en live (l'API publique throttle les
# rafales). Les coords non déjà en cache renvoient {} (critères commerces/ski/calme
# en "pending" pour ces biens, sans bloquer l'export). Réchauffage : warm.py.
_NO_LIVE_OVERPASS = bool(os.environ.get("EXPORT_NO_LIVE_OVERPASS"))


def _poi_distances(lat: float, lon: float, cache: dict) -> dict:
    if lat is None or lon is None:
        return {}
    key = f"{round(lat, 4)},{round(lon, 4)}"
    if key in cache:
        return cache[key]
    if _NO_LIVE_OVERPASS:
        return {}
    res = None
    for attempt in range(3):
        res = _query_poi(lat, lon)
        if res is not None:
            break
        time.sleep(4 * (attempt + 1))
    if res is not None:
        cache[key] = res
        try:
            with open(_POI_CACHE, "w", encoding="utf-8") as fh:
                json.dump(cache, fh)
        except Exception:
            pass
        return res
    return {}


def _load_poi_cache() -> dict:
    try:
        with open(_POI_CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


_FIBRE_LUT = os.path.join(os.path.dirname(__file__), "..", "..", "data", "fibre_communes.json")


def _load_fibre_lut() -> dict:
    try:
        with open(_FIBRE_LUT, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


_EQUIP_PATTERNS = {
    "cheminee": r"chemin[ée]e|po[êe]le|insert",
    "terrasse": r"terrasse",
    "garage": r"garage",
    "piscine": r"piscine",
    # Extérieur privatif : sert de repli au critère `jardin` quand l'annonce ne donne pas
    # de surface de terrain (14 % des maisons du set) mais décrit bien un dehors.
    # « cour » est borné (\b) pour ne pas attraper « courant », « parcours », « cuisine ».
    "jardin": r"\bjardins?\b|\bverger\b|\bpotager\b|parc\s+arbor|terrain\s+(de|d[' ]environ|attenant|clos|arbor)|parcelle\s+(de|d[' ]environ)|\bcour\b|espace\s+ext[ée]rieur",
    "vue": r"vue\s+(mer|d[ée]gag|panoram|impren|sur\s+(la\s+)?(mer|vall|campagne|montagne|oc[ée]an))|panorama|sans\s+vis-?\s?[àa]-?\s?vis\s+avec\s+vue",
    # Front de mer / première ligne : la signature « posé sur les rochers, pieds dans l'eau ».
    "bord_de_mer": r"bord\s+de\s+mer|front\s+de\s+mer|pieds?\s+dans\s+l[' ]eau|premi[èe]re\s+ligne|face\s+[àa]\s+la\s+mer|acc[èe]s\s+(direct\s+)?((?:[àa]\s+la\s+)?(mer|plage|gr[èe]ve))|surplombe\s+(la\s+mer|l[' ]oc[ée]an)|vue\s+impren\w*\s+sur\s+(la\s+)?(mer|oc[ée]an)|en\s+bord\s+d[' ]oc[ée]an",
    # Bord d'eau non maritime : rivière, étang, lac, ria, aber, estuaire, plan d'eau.
    "bord_eau": r"bord\s+de\s+(rivi[èe]re|l[' ]?[ée]tang|lac|ria|aber|fleuve|ruisseau|canal)|au\s+bord\s+de\s+l[' ]eau|bord\s+de\s+plan\s+d[' ]eau|vue\s+(sur\s+)?(rivi[èe]re|[ée]tang|lac|ria|aber|estuaire)|en\s+bord\s+de\s+(rivi[èe]re|ria|aber|estuaire)|surplombe\s+(la\s+)?(rivi[èe]re|vall[ée]e)",
    # En hauteur avec vue dégagée : promontoire, coteau, surplomb, position dominante.
    "en_hauteur": r"en\s+hauteur|sur\s+les\s+hauteurs|hauteurs\s+de\b|\bsurplomb(e|ant)?\b|\bdominant\w*\b|position\s+dominante|promontoire|belv[ée]d[èe]re|\bcoteau\b|perch[ée]e?\s+(sur|en)|point\s+(haut|culminant)",
}


def _detect_equipements(description: str | None) -> list[str]:
    if not description:
        return []
    t = description.lower()
    return [k for k, pat in _EQUIP_PATTERNS.items() if re.search(pat, t)]


# Signe « pavillon / neuf » (= peu de cachet). Conservateur : on évite « cuisine neuve »
# (rénovation = bien) en exigeant maison/villa/construction neuve, VEFA, programme neuf, etc.
_PAVILLON_RE = re.compile(
    r"\bpavillon\b|(?:maison|villa|construction|b[âa]tisse)\s+neuve|\bvefa\b|"
    r"\brt\s?2012\b|à\s+construire|programme\s+neuf|construction\s+récente|"
    r"maison\s+r[ée]cente|villa\s+contemporaine", re.I)


# Le produit « lotissement / parcelle viabilisée » est l'autre face du pavillonnaire :
# même absence de cachet, même voisinage résidentiel. Détecté à part car il faut écarter
# les négations — « hors lotissement », « non viabilisé » sont au contraire des arguments
# de vente ici (62 annonces du secteur sur 167 mentions).
_LOTISSEMENT_RE = re.compile(
    r"\blotissement\b|\bviabilis[ée]\w*|zone\s+pavillonnaire|"
    r"quartier\s+r[ée]sidentiel|zone\s+r[ée]sidentiel\w*", re.I)
_LOTISSEMENT_NEG_RE = re.compile(
    r"(?:hors|pas\s+de|pas\s+en|non|aucun|sans|ni)\s+(?:\w+\s+){0,2}?"
    r"(?:lotissement|viabilis|zone\s+pavillonnaire)", re.I)


def _detect_pavillon_neuf(description: str | None) -> bool:
    if not description:
        return False
    if _PAVILLON_RE.search(description):
        return True
    return bool(_LOTISSEMENT_RE.search(description)
                and not _LOTISSEMENT_NEG_RE.search(description))


def _fibre_flags(code_commune: str | None, lut: dict) -> dict:
    if not code_commune or code_commune not in lut:
        return {}
    pct = lut[code_commune]
    return {"fibre": pct >= 50, "fibre_pct": pct}


# --- Viager : exclu du dataset (bien noté à tort car le prix affiché = bouquet, pas
# le coût réel ; le bien reste occupé). Détection sur le texte de l'annonce. ---------
_VIAGER_RE = re.compile(
    r"\bviager\b|nue?[- ]?propri[ée]t[ée]|rente\s+viag|occup[ée]\s+au\s+profit|"
    r"droit\s+d.usage\s+et\s+d.habitation|vente\s+à\s+terme\s+occup", re.I)
# Facteur appliqué au match d'un viager (0.15 -> un match de 80 tombe à 12).
_VIAGER_MATCH_FACTOR = 0.15


def _detect_viager(*texts: str | None) -> bool:
    return any(t and _VIAGER_RE.search(t) for t in texts)


# Résidence de tourisme / services sous bail commercial (leaseback, LMNP géré,
# Censi-Bouvard) : jouissance restreinte, gestion imposée, revente difficile -> pénalisé
# comme le viager. NB : "résidence secondaire/principale" ne matche pas (voulu).
_RESID_TOURISME_RE = re.compile(
    r"r[ée]sidence\s+(?:de\s+)?tourisme|r[ée]sidence\s+de\s+vacances|r[ée]sidence\s+services?|"
    r"r[ée]sidence\s+g[ée]r[ée]e|r[ée]sidence\s+(?:senior|[ée]tudiante)|bail\s+commercial|"
    r"censi[- ]bouvard", re.I)
_RESID_MATCH_FACTOR = 0.2  # un match de 80 tombe à 16


def _detect_residence_tourisme(*texts: str | None) -> bool:
    return any(t and _RESID_TOURISME_RE.search(t) for t in texts)


# Déjà sous compromis / sous offre : le bien n'est plus à vendre. Montrer au groupe une
# maison qu'il ne peut pas acheter est pire qu'un viager — il n'y a même pas de décision
# à prendre. Vécu : la maison d'Ugine, 180 000 €, publiée comme pépite à 83,7, dont
# l'annonce commence par « SOUS COMPROMIS ».
#
# Motif volontairement étroit. « vendu » seul attrape « vendu meublé », « vendu avec
# locataire en place », « vendu par notre partenaire foncier » — 260 faux positifs contre
# 75 vraies annonces retirées du marché.
_SOUS_COMPROMIS_RE = re.compile(
    r"sous\s+(?:un\s+)?(?:compromis|offre|promesse)|compromis\s+sign|promesse\s+sign|"
    r"offre\s+accept[ée]|bien\s+vendu\b|d[ée]j[àa]\s+vendu", re.I)
_COMPROMIS_MATCH_FACTOR = 0.1  # un match de 80 tombe à 8


def _detect_sous_compromis(*texts: str | None) -> bool:
    return any(t and _SOUS_COMPROMIS_RE.search(t) for t in texts)


# Mobil-home, chalet de camping, habitation légère de loisirs : même problème
# économique que le viager et la résidence de tourisme, et c'est pourquoi c'est traité
# au même endroit — on n'achète pas le sol, l'occupation est saisonnière et réglementée,
# la revente se fait à perte. Vécu : « ce chalet de montagne de 4 pièces de 35 m² situé
# au camping "la motte flottante" », 75 000 €, entré dans les pépites à 81,3.
#
# « Camping » seul ne suffit pas — « à 2 km d'un camping » est un argument de voisinage.
# Il faut que le bien soit DANS le camping, ou qu'il se nomme lui-même.
_MOBILHOME_RE = re.compile(
    r"mobil[- ]?home|mobilhome|habitation\s+l[ée]g[èe]re\s+de\s+loisirs?|\bhll\b|"
    r"r[ée]sidence\s+mobile\s+de\s+loisirs?|parc\s+r[ée]sidentiel\s+de\s+loisirs?|\bprl\b|"
    r"(?:au|dans|sur)\s+(?:le\s+|un\s+)?(?:camping|terrain\s+de\s+camping)\b|"
    r"au\s+sein\s+d[' ]un\s+camping|emplacement\s+(?:n[°o]|de\s+camping)|"
    r"chalet\s+de\s+loisirs?", re.I)
_MOBILHOME_MATCH_FACTOR = 0.2  # un match de 80 tombe à 16


def _detect_mobilhome(*texts: str | None) -> bool:
    return any(t and _MOBILHOME_RE.search(t) for t in texts)


_TENSION_LUT = os.path.join(os.path.dirname(__file__), "..", "..", "data", "tension_communes.json")


def _load_tension_lut() -> dict:
    try:
        with open(_TENSION_LUT, encoding="utf-8") as fh:
            return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    except Exception:
        return {}


def _norm_commune(name: str | None) -> str:
    """Normalise un nom de commune pour le lookup tension (sans accents ni tirets)."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return re.sub(r"\s+", " ", s)


def _tension_flags(commune: str | None, lut: dict) -> dict:
    v = lut.get(_norm_commune(commune))
    return {"tension_score": v} if v is not None else {}


def _query_overpass(lat: float, lon: float) -> dict | None:
    # Une seule requête : autoroute/voie ferrée/route passante (bruit) + sentiers (rando).
    # Les routes principales (nationales, départementales structurantes) sont cherchées
    # dans un rayon plus court : leur bruit porte moins loin, et à 2,5 km on en trouve
    # partout — le critère ne trierait plus rien.
    q = (f'[out:json][timeout:25];('
         f'way(around:2500,{lat},{lon})[highway~"motorway|trunk"];'
         f'way(around:2500,{lat},{lon})[railway=rail];'
         f'way(around:800,{lat},{lon})[highway~"^(primary|secondary)$"];'
         f'way(around:1500,{lat},{lon})[highway~"path|footway|bridleway"];'
         f'relation(around:3000,{lat},{lon})[route=hiking];);out geom 300;')
    try:
        data = urllib.parse.urlencode({"data": q}).encode()
        req = urllib.request.Request(_OVERPASS, data=data, headers={"User-Agent": _UA_OVERPASS})
        with urllib.request.urlopen(req, timeout=40) as r:
            payload = json.loads(r.read())
    except Exception:
        return None
    best = {"dist_autoroute_m": None, "dist_rail_m": None, "dist_route_m": None,
            "infra_checked": True}
    n_sentiers, n_routes = 0, 0
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        if el.get("type") == "relation" and tags.get("route") == "hiking":
            n_routes += 1
            continue
        hw = tags.get("highway")
        if hw in ("path", "footway", "bridleway"):
            n_sentiers += 1
            continue
        key = ("dist_autoroute_m" if hw in ("motorway", "trunk")
               else "dist_route_m" if hw in ("primary", "secondary")
               else "dist_rail_m" if tags.get("railway") == "rail" else None)
        if not key:
            continue
        for p in el.get("geometry", []):
            dm = round(haversine_km(lat, lon, p["lat"], p["lon"]) * 1000)
            if best[key] is None or dm < best[key]:
                best[key] = dm
    best["rando_count"] = n_sentiers + n_routes
    best["randonnee"] = (n_routes >= 1) or (n_sentiers >= 3)
    return best


def _infra_distances(lat: float, lon: float, cache: dict) -> dict:
    """Distances autoroute/voie ferrée (m), via cache puis Overpass. {} si indéterminé."""
    if lat is None or lon is None:
        return {}
    key = f"{round(lat, 4)},{round(lon, 4)}"
    if key in cache:
        return cache[key]
    if _NO_LIVE_OVERPASS:
        return {}
    res = None
    for attempt in range(3):  # Overpass throttle parfois : on réessaie poliment
        res = _query_overpass(lat, lon)
        if res is not None:
            break
        time.sleep(4 * (attempt + 1))
    if res is not None:
        cache[key] = res
        try:
            with open(_INFRA_CACHE, "w", encoding="utf-8") as fh:
                json.dump(cache, fh)
        except Exception:
            pass
        return res
    return {}


# --- Relief : proéminence locale (à quel point le point DOMINE ses alentours) --------
# L'altitude absolue ne dit pas si un terrain est « surélevé » (la côte est basse) ;
# on échantillonne une couronne autour du point et on mesure l'écart d'altitude.
_RELIEF_CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "relief_cache.json")
_IGN_ALTI_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"
_NO_LIVE_RELIEF = bool(os.environ.get("EXPORT_NO_LIVE_RELIEF"))


def _load_relief_cache() -> dict:
    try:
        with open(_RELIEF_CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _query_prominence(lat: float, lon: float, radius_m: int = 300) -> dict | None:
    """Altitude du point − moyenne d'une couronne (8 points, rayon r). +.= dominant."""
    import math

    pts = [(lat, lon)]
    for k in range(8):
        a = 2 * math.pi * k / 8
        pts.append((lat + radius_m * math.cos(a) / 111320,
                    lon + radius_m * math.sin(a) / (111320 * math.cos(math.radians(lat)))))
    lats = "|".join(str(round(a, 6)) for a, _ in pts)
    lons = "|".join(str(round(b, 6)) for _, b in pts)
    try:
        r = urllib.request.Request(
            f"{_IGN_ALTI_URL}?{urllib.parse.urlencode({'lat': lats, 'lon': lons, 'resource': 'ign_rge_alti_wld', 'delimiter': '|', 'zonly': 'true'})}",
            headers={"User-Agent": _UA})
        with urllib.request.urlopen(r, timeout=25) as resp:
            elevs = json.loads(resp.read()).get("elevations", [])
    except Exception:
        return None
    if not elevs or elevs[0] is None or elevs[0] < -1000:
        return None
    pt = elevs[0]
    neigh = [e for e in elevs[1:] if e is not None and e > -1000]  # filtre no-data (mer/hors zone)
    if not neigh:
        return None
    return {"prominence_m": round(pt - sum(neigh) / len(neigh), 1)}


def _relief_prominence(lat: float, lon: float, cache: dict) -> dict:
    if lat is None or lon is None:
        return {}
    key = f"{round(lat, 4)},{round(lon, 4)}"
    if key in cache:
        return cache[key]
    if _NO_LIVE_RELIEF:
        return {}
    res = None
    for attempt in range(3):
        res = _query_prominence(lat, lon)
        if res is not None:
            break
        time.sleep(2 * (attempt + 1))
    if res is not None:
        cache[key] = res
        try:
            with open(_RELIEF_CACHE, "w", encoding="utf-8") as fh:
                json.dump(cache, fh)
        except Exception:
            pass
        return res
    return {}


# --- Distance à la mer : le modèle d'altitude IGN est "terre seule" -> la mer renvoie
# du no-data. On échantillonne des rayons ; le 1er point no-data = la côte. Overpass
# (natural=coastline) serait plus direct mais est injoignable depuis le conteneur. ------
_SEA_CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "sea_cache.json")


def _load_sea_cache() -> dict:
    try:
        with open(_SEA_CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _query_sea_distance(lat: float, lon: float, maxm: int = 3000, step: int = 250, bearings: int = 8) -> dict | None:
    """Distance à la mer (m) via échantillonnage IGN. None si erreur ; {dist_mer_m: >maxm}
    si aucune mer trouvée dans le rayon (= intérieur des terres)."""
    import math

    pts, dist = [], []
    for b in range(bearings):
        a = 2 * math.pi * b / bearings
        for d in range(step, maxm + 1, step):
            pts.append((lat + d * math.cos(a) / 111320,
                        lon + d * math.sin(a) / (111320 * math.cos(math.radians(lat)))))
            dist.append(d)
    best = None
    ok = False
    for i in range(0, len(pts), 24):
        lats = "|".join(str(round(a, 6)) for a, _ in pts[i:i + 24])
        lons = "|".join(str(round(b, 6)) for _, b in pts[i:i + 24])
        elevs = None
        for k in range(3):
            try:
                req = urllib.request.Request(
                    f"{_IGN_ALTI_URL}?{urllib.parse.urlencode({'lat': lats, 'lon': lons, 'resource': 'ign_rge_alti_wld', 'delimiter': '|', 'zonly': 'true'})}",
                    headers={"User-Agent": _UA})
                with urllib.request.urlopen(req, timeout=25) as resp:
                    elevs = json.loads(resp.read()).get("elevations", [])
                break
            except Exception:
                time.sleep(1.5 * (k + 1))
        if elevs is None:
            return None  # échec réseau -> on ne cache pas (à réessayer)
        ok = True
        for j, val in enumerate(elevs):
            if val is not None and val < -1000:  # no-data IGN = mer
                d = dist[i + j]
                best = d if best is None else min(best, d)
    if not ok:
        return None
    return {"dist_mer_m": best if best is not None else maxm + 1}


def _sea_distance(lat: float, lon: float, cache: dict, *, live: bool = False) -> dict:
    """Cache-only par défaut (lecture rapide à l'export) ; live=True pour le réchauffage."""
    if lat is None or lon is None:
        return {}
    key = f"{round(lat, 4)},{round(lon, 4)}"
    if key in cache:
        return cache[key]
    if not live:
        return {}
    res = _query_sea_distance(lat, lon)
    if res is not None:
        cache[key] = res
        try:
            with open(_SEA_CACHE, "w", encoding="utf-8") as fh:
                json.dump(cache, fh)
        except Exception:
            pass
        return res
    return {}


# --- Ensoleillement : ce que le relief laisse passer de la course du soleil -----------
# En vallée alpine c'est LE critère que l'annonce ne donne pas : deux maisons distantes
# d'un kilomètre ne passent pas le même hiver selon qu'elles sont à l'adret ou à l'ubac,
# et l'altitude — identique sur les deux versants — n'en dit rien. Le calcul vit dans
# `services/soleil.py` (pur, testable hors ligne) ; ici on ne fait que l'échantillonnage
# IGN et le cache, comme pour la proéminence et la distance à la mer.
#
# 87 points par bien, soit 4 requêtes groupées : trop cher pour l'export d'un gros
# catalogue, d'où le même contrat que la mer — cache-only à l'export, réchauffé à part
# (`scripts/warm_ensoleillement.py`).
_SOLEIL_CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "soleil_cache.json")


def _load_soleil_cache() -> dict:
    try:
        with open(_SOLEIL_CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _query_altitudes(points: list[tuple[float, float]]) -> list | None:
    """Altitudes IGN d'une liste de points, par lots de 24. None si un lot échoue.

    Un lot manquant décalerait tous les points suivants (la mesure est positionnelle) :
    mieux vaut abandonner le point et le redemander au prochain réchauffage.
    """
    out: list = []
    for i in range(0, len(points), 24):
        lot = points[i:i + 24]
        lats = "|".join(str(round(a, 6)) for a, _ in lot)
        lons = "|".join(str(round(b, 6)) for _, b in lot)
        elevs = None
        for k in range(3):
            try:
                req = urllib.request.Request(
                    f"{_IGN_ALTI_URL}?{urllib.parse.urlencode({'lat': lats, 'lon': lons, 'resource': 'ign_rge_alti_wld', 'delimiter': '|', 'zonly': 'true'})}",
                    headers={"User-Agent": _UA})
                with urllib.request.urlopen(req, timeout=25) as resp:
                    elevs = json.loads(resp.read()).get("elevations", [])
                break
            except Exception:
                time.sleep(1.5 * (k + 1))
        if not elevs or len(elevs) != len(lot):
            return None
        out.extend(elevs)
    return out


def _query_soleil(lat: float, lon: float) -> dict | None:
    from .soleil import mesurer, points_a_mesurer

    altitudes = _query_altitudes(points_a_mesurer(lat, lon))
    if altitudes is None:
        return None  # échec réseau -> on ne cache pas (à réessayer)
    return mesurer(lat, altitudes)


def _soleil(lat: float, lon: float, cache: dict, *, live: bool = False) -> dict:
    """Cache-only par défaut (lecture rapide à l'export) ; live=True pour le réchauffage."""
    if lat is None or lon is None:
        return {}
    key = f"{round(lat, 4)},{round(lon, 4)}"
    if key in cache:
        return cache[key]
    if not live:
        return {}
    res = _query_soleil(lat, lon)
    if res is not None:
        cache[key] = res
        try:
            with open(_SOLEIL_CACHE, "w", encoding="utf-8") as fh:
                json.dump(cache, fh)
        except Exception:
            pass
        return res
    return {}


# --- Attractivité locative saisonnière (« Airbnb ») ----------------------------------
# Ce qu'un logement peut se louer à la semaine ici : la remontée mécanique (l'hiver), le
# lac et les sites (l'été), l'hébergement touristique déjà installé (le marché existe) et
# les restaurants (ce dont un locataire a besoin sur place). Le barème vit dans
# `services/tourisme.py`, pur et testable ; ici on ne fait que l'échantillonnage OSM.
#
# Même contrat que la mer et le soleil : une requête Overpass par point, ~5 s, donc
# CACHE SEUL à l'export et réchauffage à part (`scripts/warm_tourisme.py`). Le piège est
# le même partout ici — sans réchauffage le critère sort en `pending`, il est alors
# *exclu* du score au lieu de le baisser, et rien ne le dit.
_TOURISME_CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "tourisme_cache.json")
# Surface minimale d'un plan d'eau pour compter comme « lac ». Sans ce seuil, la mare de
# 20 m² du hameau d'à côté valait le lac d'Annecy : autour de Beaufort, Overpass rend
# 72 plans d'eau dont 70 sont des retenues d'alpage.
_LAC_MIN_HA = 5.0
_HEBERGEMENTS = ("hotel", "guest_house", "chalet", "apartment", "hostel", "motel",
                 "camp_site", "alpine_hut", "wilderness_hut")
_ATTRACTIONS = ("attraction", "viewpoint", "museum", "theme_park")
_RESTOS = ("restaurant", "cafe", "bar", "pub")


def _load_tourisme_cache() -> dict:
    try:
        with open(_TOURISME_CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _bbox_ha(bb: dict) -> float:
    h = haversine_km(bb["minlat"], bb["minlon"], bb["maxlat"], bb["minlon"]) * 1000
    w = haversine_km(bb["minlat"], bb["minlon"], bb["minlat"], bb["maxlon"]) * 1000
    return h * w / 10000


def _dist_bbox_m(lat: float, lon: float, bb: dict) -> int:
    """Distance au rectangle englobant (0 si le point est dedans).

    Pour un lac de 10 000 ha, le CENTRE est à 10 km du rivage : mesurer la distance au
    centroïde faisait passer Aix-les-Bains pour une commune sans lac. Le rectangle est
    une approximation grossière du rivage, mais du bon côté de l'erreur.
    """
    la = min(max(lat, bb["minlat"]), bb["maxlat"])
    lo = min(max(lon, bb["minlon"]), bb["maxlon"])
    return round(haversine_km(lat, lon, la, lo) * 1000)


def _query_tourisme(lat: float, lon: float) -> dict | None:
    q = (f'[out:json][timeout:90];('
         f'nwr(around:5000,{lat},{lon})[tourism~"^({"|".join(_HEBERGEMENTS)})$"];'
         f'nwr(around:3000,{lat},{lon})[amenity~"^({"|".join(_RESTOS)})$"];'
         f'nwr(around:10000,{lat},{lon})[tourism~"^({"|".join(_ATTRACTIONS)})$"];'
         f'way(around:25000,{lat},{lon})[aerialway~"^(gondola|chair_lift|cable_car|mixed_lift)$"];'
         f'nwr(around:12000,{lat},{lon})[natural=water][water~"^(lake|reservoir)$"];'
         f');out tags bb 1500;')
    try:
        data = urllib.parse.urlencode({"data": q}).encode()
        req = urllib.request.Request(_OVERPASS, data=data, headers={"User-Agent": _UA_OVERPASS})
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = json.loads(r.read())
    except Exception:
        return None
    heb = restos = attractions = 0
    ski = lac = None
    for el in payload.get("elements", []):
        t = el.get("tags", {})
        bb = el.get("bounds")
        pos = ({"minlat": el["lat"], "maxlat": el["lat"], "minlon": el["lon"], "maxlon": el["lon"]}
               if el.get("lat") is not None else bb)
        if t.get("tourism") in _HEBERGEMENTS:
            heb += 1
        elif t.get("amenity") in _RESTOS:
            restos += 1
        elif t.get("tourism") in _ATTRACTIONS:
            attractions += 1
        elif t.get("aerialway") and pos:
            d = _dist_bbox_m(lat, lon, pos)
            ski = d if ski is None else min(ski, d)
        elif t.get("natural") == "water" and bb and _bbox_ha(bb) >= _LAC_MIN_HA:
            d = _dist_bbox_m(lat, lon, bb)
            lac = d if lac is None else min(lac, d)
    return {"tour_hebergements": heb, "tour_restos": restos, "tour_attractions": attractions,
            "tour_dist_remontee_m": ski, "tour_dist_lac_m": lac, "tour_checked": True}


def _tourisme(lat: float, lon: float, cache: dict, *, live: bool = False) -> dict:
    """Cache-only par défaut (lecture rapide à l'export) ; live=True pour le réchauffage."""
    if lat is None or lon is None:
        return {}
    key = f"{round(lat, 4)},{round(lon, 4)}"
    if key in cache:
        return cache[key]
    if not live:
        return {}
    res = _query_tourisme(lat, lon)
    if res is not None:
        cache[key] = res
        try:
            with open(_TOURISME_CACHE, "w", encoding="utf-8") as fh:
                json.dump(cache, fh)
        except Exception:
            pass
        return res
    return {}


# Optimisation : galerie web -> 1280 px max suffit ; JPEG progressif qualité 78.
_MAX_DIM = 1280
_JPEG_QUALITY = 78


def _optimize_jpeg(data: bytes) -> bytes:
    """Redimensionne (≤_MAX_DIM) et recompresse en JPEG ; renvoie l'original si échec."""
    if Image is None:
        return data
    try:
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGB")  # supprime alpha/EXIF, force JPEG-compatible
        im.thumbnail((_MAX_DIM, _MAX_DIM))  # garde le ratio, ne sur-échantillonne pas
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True, progressive=True)
        out = buf.getvalue()
        return out if out and len(out) < len(data) else data
    except Exception:
        return data


class _RowItem:
    """Adapte une ligne DB Listing à l'objet `item` attendu par evaluate() (.flags)."""

    def __init__(self, row: Listing, extra_flags: dict | None = None):
        self.prix = row.prix
        self.type_bien = row.type_bien
        self.nb_chambres = row.nb_chambres
        self.nb_pieces = row.nb_pieces
        self.surface_terrain = row.surface_terrain
        self.surface_bati = row.surface_bati
        self.dpe_classe = row.dpe_classe   # lu par le critère `dpe`
        self.latitude = row.latitude
        self.longitude = row.longitude
        self.flags = {c: getattr(row, c) for c in _FLAG_COLS}
        if extra_flags:
            self.flags.update(extra_flags)


def _pref_dump(pref) -> dict:
    """Sérialise une préférence, `id` compris : le libellé change avec les paramètres et
    d'un set à l'autre, l'id non — c'est lui qui porte les poids personnels (cf.
    services/criteres.py)."""
    if isinstance(pref, dict):
        kind, params = pref.get("kind"), pref.get("params") or {}
        out = {"kind": kind, "label": pref.get("label") or kind,
               "weight": pref.get("weight", 1.0), "params": params}
    else:
        kind, params = getattr(pref, "kind", None), getattr(pref, "params", {}) or {}
        out = {"kind": kind, "label": getattr(pref, "label", None),
               "weight": getattr(pref, "weight", 1.0), "params": params}
    out["id"] = identifiant(kind, params)
    return out


def _photo_urls(row: Listing) -> list[str]:
    """Extrait les URLs de photos du payload source (best-effort, multi-source)."""
    raw = row.raw if isinstance(row.raw, dict) else {}
    urls: list[str] = []
    images = raw.get("photos") or raw.get("images") or []
    # Leboncoin : `images` est un dict {nb_images, urls, urls_large, urls_thumb}
    # (et non une liste). On privilégie les grandes images.
    if isinstance(images, dict):
        images = images.get("urls_large") or images.get("urls") or images.get("urls_thumb") or []
    for ph in images:
        if isinstance(ph, str):
            urls.append(ph)
        elif isinstance(ph, dict):
            u = ph.get("url") or ph.get("url_photo") or ph.get("urlThumbnail") or ph.get("href")
            if u:
                urls.append(u)
    # dédoublonne en gardant l'ordre
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:_MAX_PHOTOS]


def _download_photos(row: Listing, photos_dir: str, rel_base: str) -> list[str]:
    """Télécharge les photos en local ; renvoie les chemins relatifs (depuis data.json)."""
    key = f"{row.source}_{row.external_id}".replace("/", "_")
    dest_dir = os.path.join(photos_dir, key)
    rels: list[str] = []
    for i, url in enumerate(_photo_urls(row)):
        rel = f"{rel_base}/{key}/{i}.jpg"
        path = os.path.join(dest_dir, f"{i}.jpg")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            rels.append(rel)
            continue
        try:
            os.makedirs(dest_dir, exist_ok=True)
            req = urllib.request.Request(url, headers={"User-Agent": _UA, "Referer": row.url or ""})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            if data:
                data = _optimize_jpeg(data)
                with open(path, "wb") as fh:
                    fh.write(data)
                rels.append(rel)
        except Exception:
            continue  # photo indisponible -> on saute, sans casser l'export
    if not rels and os.path.isdir(dest_dir):
        # Pas d'URL source (ex. DB reconstruite par le seed) mais photos déjà présentes
        # sur disque -> on réutilise les fichiers locaux (pas de perte, pas de re-DL).
        files = sorted(
            (f for f in os.listdir(dest_dir) if f.endswith(".jpg")),
            key=lambda f: int(f[:-4]) if f[:-4].isdigit() else 9999,
        )
        rels = [f"{rel_base}/{key}/{f}" for f in files]
    return rels


def _biens_publies(chemin: str) -> set:
    """Les (source, external_id) présents dans un data.json — pour republier un set à
    l'identique au lieu de le recouper."""
    try:
        with open(chemin, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return set()
    return {(b.get("source"), b.get("external_id")) for b in data.get("biens", [])}


def _dans_la_zone(row, zone: dict | None) -> bool:
    """Le bien tombe-t-il dans la zone géographique déclarée par le set ?

    La zone appartient au SET, pas aux biens. Le réflexe inverse — retirer le set des
    biens hors zone — se retourne contre soi : `set_ids` vide signifie « noté pour TOUS
    les sets » (rétro-compatibilité des agences), si bien que retirer un bien du set
    têtard le faisait noter pour têtard, Pauline ET la Bretagne. Vécu sur 2 579 biens.

    Déclarée ici, la zone se réapplique d'elle-même à chaque collecte, sans qu'il faille
    repasser sur la base.
    """
    if not zone:
        return True
    if zone.get("est_axe_lyon_valence"):
        from .geo import est_a_lest_du_rhone

        cote = est_a_lest_du_rhone(row.latitude, row.longitude)
        # Géoloc manquante : on ne retire pas un bien sur une mesure absente.
        if cote is False:
            return False
    return True


def _passes_pepites_gate(scores_by_set: dict, member: set, seuils: dict,
                         conserver: dict | None = None, cle: tuple | None = None) -> bool:
    """Filtre « pépites » : un bien n'est gardé que s'il tient le seuil de CHAQUE set
    resserré auquel il appartient. Un bien membre d'aucun set resserré (ex. Pauline
    quand on resserre têtard et le littoral) est toujours conservé.

    Plusieurs seuils, et pas un seul, parce que la base est partagée : elle garde tout
    le catalogue de chaque set alors que data.json n'en publie que le haut du panier.
    Resserrer un seul set à l'export ferait revenir en bloc le catalogue complet des
    autres — le resserrage breton (690 biens ramenés à 12) serait annulé par la
    première collecte têtard venue.
    """
    # Sets republiés à l'identique : le score ne décide plus, l'appartenance à la
    # publication précédente décide. Sert quand une correction de données a déplacé les
    # scores d'un set qu'on ne veut PAS recouper au passage — recouper le set de
    # quelqu'un d'autre sans le lui dire n'est pas une décision qui se prend à l'export.
    for set_id, garder in (conserver or {}).items():
        if member and set_id not in member:
            continue
        if cle not in garder:
            return False
    for set_id, seuil in (seuils or {}).items():
        if member and set_id not in member:
            continue  # hors de ce set -> non concerné par SON resserrage
        sc = (scores_by_set.get(str(set_id)) or {}).get("match_score")
        if not isinstance(sc, (int, float)) or sc < seuil:
            return False
    return True


def _seuils_pepites(min_match_score: float | None, primary_set_id: int | None,
                    pepites: dict | None) -> dict:
    """Normalise les deux façons de demander un resserrage en un {set_id: seuil}."""
    seuils = dict(pepites or {})
    if min_match_score is not None and primary_set_id is not None:
        seuils.setdefault(int(primary_set_id), float(min_match_score))
    return seuils


# Champs qui font la complétude d'une ligne : entre deux copies du même bien, on garde
# celle qui en renseigne le plus. Pas la mieux notée — ce serait choisir le score le plus
# flatteur plutôt que le plus fiable. Mesuré sur un doublon réel (Villes, 01) : la copie
# complète notait 78,0 et la copie lacunaire 84,0, parce qu'un bien peu mesuré est jugé
# sur les seuls critères qu'on a pu lui appliquer.
def _round_or_none(value, base: int):
    return None if value is None else int(round(value / base) * base)


_COMPLETUDE = ("nb_chambres", "nb_pieces", "surface_bati", "surface_terrain", "dpe_classe",
               "condition", "code_commune", "latitude", "description")


def _completude(row) -> tuple:
    renseignes = sum(1 for c in _COMPLETUDE if getattr(row, c, None) is not None)
    return (renseignes, len(row.description or ""), len(_photo_urls(row)))


def _dedupe_rows(rows: list, log=None, preserver: set | None = None) -> list:
    """Une seule ligne par bien réel, toutes sources confondues.

    Le dédoublonnage existait déjà, mais uniquement dans l'API de recherche live — jamais
    à l'export, qui est pourtant ce qui produit le site. Résultat : le même bien vu par
    Leboncoin ET bienici occupait deux places dans les pépites publiées, avec deux scores
    différents (Chalencon apparaissait trois fois).

    Le prix entre dans la clé, arrondi à 2 % : deux annonces du même bien portent en
    pratique le même prix, alors que deux maisons voisines de surfaces comparables — que
    la géo à 110 m près ne distingue pas — n'ont aucune raison d'être au même euro.
    """
    # Identités déjà publiées : elles GAGNENT le duel, quelle que soit leur complétude.
    # Les votes du groupe sont attachés au couple (source, external_id) : fusionner une
    # copie publiée dans une autre la ferait disparaître du site et emporterait ses votes.
    # Vécu : le set breton est passé de 12 à 11 pépites et Pauline de 162 à 120 biens au
    # premier export dédoublonné.
    preserver = preserver or set()

    def _cle(row) -> tuple:
        # La commune, pas les coordonnées. L'empreinte de `dedup.py` géolocalise à 110 m
        # près, ce qui suffit à séparer deux annonces du MÊME bien : les portails ne le
        # placent pas au même endroit (Bien'ici floute), et Chalencon est resté deux fois
        # dans les pépites pour 100 m d'écart. La commune tolère ce flou, et les surfaces
        # + le prix (second temps) referment la porte : deux maisons de 140 m² au même
        # euro dans la même commune sont le même bien.
        return (row.code_commune or row.commune or "",
                (row.type_bien or "").lower(),
                _round_or_none(row.surface_bati, 10),
                _round_or_none(row.surface_terrain, 100))

    groupes: dict[tuple, list] = {}
    for row in rows:
        groupes.setdefault(_cle(row), []).append(row)

    gardes, fusionnes = [], 0
    for membres in groupes.values():
        # Deuxième temps : à empreinte égale, on ne fusionne que les prix voisins. Une
        # tranche de prix dans la clé aurait séparé deux annonces au bord de la tranche
        # (152 400 et 152 600 €), ce qui est le contraire du but.
        restants = sorted(membres,
                          key=lambda r: ((r.source, r.external_id) in preserver, _completude(r)),
                          reverse=True)
        while restants:
            chef = restants.pop(0)
            gardes.append(chef)
            if chef.prix is None:
                continue
            proches = [r for r in restants
                       if r.prix is not None and abs(r.prix - chef.prix) <= 0.02 * chef.prix]
            for r in proches:
                restants.remove(r)
            fusionnes += len(proches)
    if fusionnes and log:
        log(f"dédoublonnage : {fusionnes} doublons inter-sources fusionnés "
            f"({len(rows)} lignes -> {len(gardes)} biens)")
    return gardes


def _zone_de(row, zones: list[dict] | None) -> str | None:
    """Zone de comparaison à laquelle ce bien appartient : la plus proche, si elle
    l'atteint. Partition de Voronoï bornée par le rayon, donc un bien et un seul par
    zone — et rien pour les biens qui tombent entre deux massifs."""
    if not zones or row.latitude is None or row.longitude is None:
        return None
    proche, dmin = None, None
    for z in zones:
        d = haversine_km(row.latitude, row.longitude, z["lat"], z["lon"])
        if d <= z.get("rayon_km", 30) and (dmin is None or d < dmin):
            proche, dmin = z.get("nom"), d
    return proche


def _meilleurs_par_zone(prepares: list, planchers: dict, log=None) -> set:
    """La clé du bien le mieux noté de CHAQUE zone, pour les sets qui le demandent.

    Publier le haut du panier, et lui seul, ne montre qu'une chose : les secteurs où le
    budget achète quelque chose. Le groupe n'a alors aucun moyen de voir ce que 250 k€
    donnent — ou ne donnent pas — en Tarentaise, au bord du Léman ou dans le Queyras. Un
    témoin par zone rend la comparaison possible ; c'est ce qui a été demandé le
    31 août, « même si son score est bas ».

    « Bas » n'est pas « n'importe quoi » : le témoin doit DÉPASSER le plancher du set,
    70 par défaut — c'est-à-dire avoir passé les trois paliers qui s'appliquent là
    (dans le budget, pas de rénovation complète, un jardin). Une zone dont rien ne
    dépasse ce plancher n'a pas de témoin, et cette absence est elle-même la réponse.
    """
    meilleurs: dict = {}
    zones_vues: dict = {}
    for prep in prepares:
        for set_id, plancher in planchers.items():
            if prep["member"] and set_id not in prep["member"]:
                continue
            zone = (prep["zones"] or {}).get(set_id)
            if not zone:
                continue
            zones_vues.setdefault(set_id, set()).add(zone)
            sc = (prep["scores_by_set"].get(str(set_id)) or {}).get("match_score")
            # DÉPASSER le plancher, pas l'atteindre — et la nuance décide.
            # `appliquer_exigences` ramène un bien recalé au palier EXACT : une ruine
            # hors budget et sans jardin sort à 70,0 tout rond. Un témoin choisi avec
            # « >= 70 » serait donc, dans les zones pauvres, précisément le bien que les
            # paliers viennent d'écarter. Avec « > 70 », le témoin a forcément passé les
            # trois paliers de 70 : dans le budget, pas de rénovation complète, un jardin.
            if not isinstance(sc, (int, float)) or sc <= plancher:
                continue
            cle = (set_id, zone)
            if cle not in meilleurs or sc > meilleurs[cle][0]:
                meilleurs[cle] = (sc, prep["cle"])
    if log:
        for set_id, vues in zones_vues.items():
            retenues = {z for (sid, z) in meilleurs if sid == set_id}
            vides = sorted(vues - retenues)
            log(f"témoins de zone (set {set_id}) : {len(retenues)} zones publiées"
                + (f" · {len(vides)} sans rien au-dessus de {planchers[set_id]:g} : "
                   + ", ".join(vides) if vides else ""))
    return {cle for _, cle in meilleurs.values()}


def build_dataset(db, *, out_dir: str | None = None, download_photos: bool = False,
                  min_match_score: float | None = None, primary_set_id: int | None = None,
                  pepites: dict | None = None, conserver: dict | None = None,
                  meilleur_par_zone: dict | None = None) -> dict:
    """Construit le dataset statique. Si download_photos, écrit les images sous out_dir.

    Mode « pépites » (optionnel), deux écritures équivalentes :
    - `min_match_score` + `primary_set_id` : un seul set resserré ;
    - `pepites={set_id: seuil}` : plusieurs, ce qu'il faut dès que deux sets publient
      leur haut du panier depuis la même base.

    Les biens des sets non resserrés sont préservés.

    `meilleur_par_zone={set_id: plancher}` : publie EN PLUS le meilleur bien de chaque
    zone déclarée par le set, même sous le seuil des pépites, à condition qu'il tienne le
    plancher. Sert à comparer les régions entre elles (voir `_meilleurs_par_zone`).
    """
    sets = (
        db.query(FilterSet)
        .order_by(FilterSet.parent_id.isnot(None), FilterSet.id)
        .all()
    )
    seuils = _seuils_pepites(min_match_score, primary_set_id, pepites)
    set_zones: dict[int, dict] = {}
    # Zones de COMPARAISON (massifs), à ne pas confondre avec `set_zones` (le filtre
    # géographique du set). Les premières servent à publier un témoin par région, la
    # seconde à écarter les biens hors périmètre.
    set_comparaison: dict[int, list] = {}
    # Sous-sets par parent : un bien du set parent appartient à ses sous-sets, qui n'en
    # changent que la pondération. Exiger qu'il les liste explicitement dans `set_ids`
    # laissait 1 708 biens invisibles pour le sous-set « Léo » — donc un sous-set vide
    # sur le site, alors que le bien concerne les deux.
    enfants: dict[int, set] = {}
    set_prefs: dict[int, list] = {}
    set_exigences: dict[int, list] = {}
    sets_out = []
    for fs in sets:
        # Préférences RÉSOLUES : un sous-set hérite des préférences de son parent
        # (fusionnées par resolve_criteria), pour une comparaison set/sous-set fidèle.
        resolved = resolve_criteria(fs) or {}
        prefs = resolved.get("preferences") or []
        set_prefs[fs.id] = prefs
        set_exigences[fs.id] = resolved.get("exigences") or []
        set_zones[fs.id] = resolved.get("zone") or {}
        set_comparaison[fs.id] = resolved.get("zones") or []
        if fs.parent_id:
            enfants.setdefault(fs.parent_id, set()).add(fs.id)
        # property_types persisté pour le round-trip seed->export (sinon un set terrain
        # redeviendrait maison par défaut au ré-export).
        ptypes = resolved.get("property_types") or (fs.criteria or {}).get("property_types")
        sets_out.append({
            "id": fs.id, "name": fs.name, "parent_id": fs.parent_id,
            "description": fs.description,
            "property_types": ptypes or ["maison"],
            "preferences": [_pref_dump(p) for p in prefs],
            # Paliers au-delà desquels certains critères deviennent obligatoires. Persisté
            # pour le round-trip seed->export, comme property_types.
            "exigences": set_exigences.get(fs.id) or [],
        })

    saved = {(s.source, s.external_id): s for s in db.query(SavedListing).all()}

    photos_dir = os.path.join(out_dir, "photos") if out_dir else None
    if photos_dir and download_photos:
        os.makedirs(photos_dir, exist_ok=True)

    biens_out = []
    prepares: list[dict] = []
    n_viager = 0
    n_mobil = 0
    n_compromis = 0
    n_temoins = 0
    n_resid = 0
    infra_cache = _load_infra_cache()
    poi_cache = _load_poi_cache()
    relief_cache = _load_relief_cache()
    sea_cache = _load_sea_cache()
    soleil_cache = _load_soleil_cache()
    tourisme_cache = _load_tourisme_cache()
    fibre_lut = _load_fibre_lut()
    tension_lut = _load_tension_lut()
    rows = (
        db.query(Listing)
        .filter(Listing.source != "mock")
        .order_by(Listing.score.isnot(None).desc(), Listing.score.desc())
        .all()
    )
    rows = _dedupe_rows(rows, log=lambda m: print(m, flush=True),
                        preserver=_biens_publies(os.path.join(out_dir, "data.json"))
                        if out_dir else set())
    for row in rows:
        # Types conservés mais fortement déclassés (renvoyés en fond de classement) :
        # viager/nue-propriété (prix = bouquet, occupé) et résidence de tourisme sous
        # bail commercial (jouissance restreinte, gestion imposée, revente difficile).
        is_viager = _detect_viager(row.description, row.adresse)
        is_resid = _detect_residence_tourisme(row.description, row.adresse)
        is_mobil = _detect_mobilhome(row.description, row.adresse)
        is_compromis = _detect_sous_compromis(row.description, row.adresse)
        if is_viager:
            n_viager += 1
        if is_resid:
            n_resid += 1
        if is_mobil:
            n_mobil += 1
        if is_compromis:
            n_compromis += 1
        if is_compromis:
            # En tête, parce qu'un bien retiré du marché n'a plus de qualités à discuter.
            penalty = (_COMPROMIS_MATCH_FACTOR, "Déjà sous compromis / sous offre",
                       "le bien n'est plus à vendre — retiré du classement")
        elif is_viager:
            penalty = (_VIAGER_MATCH_FACTOR, "Viager / nue-propriété",
                       "viager (prix = bouquet, bien occupé) — fortement déclassé")
        elif is_resid:
            penalty = (_RESID_MATCH_FACTOR, "Résidence de tourisme / bail commercial",
                       "résidence de tourisme (bail commercial, gestion imposée) — fortement déclassé")
        elif is_mobil:
            penalty = (_MOBILHOME_MATCH_FACTOR, "Mobil-home / emplacement de camping",
                       "habitation légère de loisirs (le sol ne s'achète pas, revente à perte) "
                       "— fortement déclassé")
        else:
            penalty = None
        infra = _infra_distances(row.latitude, row.longitude, infra_cache)
        poi = _poi_distances(row.latitude, row.longitude, poi_cache)
        relief = _relief_prominence(row.latitude, row.longitude, relief_cache)
        sea = _sea_distance(row.latitude, row.longitude, sea_cache)  # cache-only (réchauffé à part)
        soleil = _soleil(row.latitude, row.longitude, soleil_cache)  # idem : réchauffé à part
        tourisme = _tourisme(row.latitude, row.longitude, tourisme_cache)  # idem
        feats = list(row.features or [])
        for e in _detect_equipements(row.description):
            if e not in feats:
                feats.append(e)
        extra = {**infra, **poi, **relief, **sea, **soleil, **tourisme,
                 **_fibre_flags(row.code_commune, fibre_lut),
                 **_tension_flags(row.commune, tension_lut),
                 "features": feats, "pavillon_neuf": _detect_pavillon_neuf(row.description)}
        item = _RowItem(row, extra_flags=extra)
        # Recalcule le score d'investissement à l'export à partir des flags courants
        # (le score stocké date de l'enrichissement -> ne refléterait pas les évolutions
        # du scoring, ex. pondération des risques). Repli sur le stocké si échec.
        try:
            _sc = compute_score(item.flags, {
                "has_text": bool(row.description or row.adresse),
                "surface_terrain": row.surface_terrain, "surface_bati": row.surface_bati,
                "type_bien": row.type_bien, "prix": row.prix,
                "latitude": row.latitude, "longitude": row.longitude,
            })
            row_score, row_score_details = _sc.score, _sc.pillars
        except Exception:
            row_score, row_score_details = row.score, row.score_details
        member = set(row.set_ids or [])  # sets d'appartenance ; vide -> tous (rétro-compat)
        for parent in list(member):
            member |= enfants.get(parent, set())
        scores_by_set = {}
        for fs_id, prefs in set_prefs.items():
            if not prefs:
                continue
            if member and fs_id not in member:
                continue  # bien hors de ce set (ex. montagne vs Pauline) -> pas de score
            if not _dans_la_zone(row, set_zones.get(fs_id)):
                continue  # hors de la zone géographique déclarée par le set
            match, details = evaluate(item, prefs, set_exigences.get(fs_id))
            if penalty and match is not None:
                # Pénalité forte : ce type plafonne très bas quelles que soient ses qualités.
                factor, plabel, pdetail = penalty
                match = round(match * factor, 1)
                details.insert(0, {"kind": "disqualifiant", "label": plabel,
                                   "weight": 0, "status": "ko", "subscore": 0, "detail": pdetail})
            scores_by_set[str(fs_id)] = {"match_score": match, "details": details}

        prepares.append({
            "row": row, "cle": (row.source, row.external_id), "member": member,
            "scores_by_set": scores_by_set, "feats": feats,
            "score": row_score, "score_details": row_score_details,
            "viager": is_viager, "residence_tourisme": is_resid,
            "zones": {fs_id: _zone_de(row, z) for fs_id, z in set_comparaison.items() if z},
        })

    # --- Couverture de mesure, par set et par critère ----------------------------------
    # « Ce critère est-il seulement mesuré ? » est la première question à se poser avant
    # de lui donner un poids : `evaluate` renormalise sur les critères notés, donc un
    # critère mesuré sur la moitié du catalogue laisse l'autre moitié être jugée sans lui.
    # La part se calcule sur le CATALOGUE du set (tout ce qu'il peut classer) et non sur
    # la sélection publiée — un panier de trente pépites ne répondrait pas à la question.
    couverture: dict[int, dict[str, int]] = {}
    n_catalogue: dict[int, int] = {}
    for prep in prepares:
        for fs_id_str, sc in prep["scores_by_set"].items():
            fs_id = int(fs_id_str)
            if sc.get("match_score") is None:
                continue
            n_catalogue[fs_id] = n_catalogue.get(fs_id, 0) + 1
            vus = couverture.setdefault(fs_id, {})
            for det in sc.get("details") or []:
                if det.get("kind") in ("exigence", "disqualifiant"):
                    continue
                if det.get("status") == "ok" and det.get("subscore") is not None:
                    # Rattachement par le LIBELLÉ : c'est la seule chose que la ligne de
                    # détail porte, et le `kind` ne suffit pas (cinq `feature` distinctes
                    # dans le set breton).
                    cle_det = det.get("label") or det.get("kind")
                    vus[cle_det] = vus.get(cle_det, 0) + 1
    for fs in sets_out:
        n = n_catalogue.get(fs["id"], 0)
        vus = couverture.get(fs["id"], {})
        for pref in fs["preferences"]:
            mesures = vus.get(pref["label"]) if pref.get("label") else None
            pref["couverture"] = round(mesures / n, 3) if (n and mesures is not None) else (0.0 if n else None)
        fs["n_catalogue"] = n

    # --- Sélection : le seuil des pépites, PLUS un témoin par zone ---------------------
    # Deux passes et non une seule, parce que « le meilleur bien de chaque zone » n'est
    # pas une décision qui se prend bien par bien : il faut avoir vu toute la zone.
    temoins = _meilleurs_par_zone(prepares, meilleur_par_zone or {},
                                  log=lambda m: print(m, flush=True)) if meilleur_par_zone else set()
    n_temoins = 0
    for prep in prepares:
        row, scores_by_set = prep["row"], prep["scores_by_set"]
        passe = not (seuils or conserver) or _passes_pepites_gate(
            scores_by_set, prep["member"], seuils, conserver, prep["cle"])
        temoin = (not passe) and prep["cle"] in temoins
        if not (passe or temoin):
            continue
        if temoin:
            n_temoins += 1
        feats, row_score, row_score_details = prep["feats"], prep["score"], prep["score_details"]
        is_viager, is_resid = prep["viager"], prep["residence_tourisme"]
        zone = next((z for z in prep["zones"].values() if z), None)

        sv = saved.get((row.source, row.external_id))
        photos = _download_photos(row, photos_dir, "photos") if (download_photos and photos_dir) else []
        biens_out.append({
            **{c: getattr(row, c) for c in _PERSIST_FLAG_COLS},  # flags persistés (round-trip)
            "id": row.id, "source": row.source, "external_id": row.external_id,
            "type_bien": row.type_bien, "prix": row.prix, "nb_chambres": row.nb_chambres,
            "nb_pieces": row.nb_pieces, "surface_terrain": row.surface_terrain,
            "surface_bati": row.surface_bati, "commune": row.commune,
            "code_postal": row.code_postal, "code_commune": row.code_commune,
            "departement": row.departement,
            "latitude": row.latitude, "longitude": row.longitude,
            "url": row.url, "description": row.description, "dpe_classe": row.dpe_classe,
            "condition": row.condition, "features": feats, "nuisances": row.nuisances,
            "altitude": row.altitude, "rail_time_min": row.rail_time_min,
            "isolement_score": row.isolement_score, "population_commune": row.population_commune,
            "risques": row.risques, "score": row_score, "score_details": row_score_details,
            "scores_by_set": scores_by_set,
            "viager": is_viager,
            "residence_tourisme": is_resid,
            "sous_compromis": is_compromis,
            # Zone de comparaison (massif) et statut de témoin : publié parce qu'il est
            # le meilleur de sa région, pas parce qu'il tient le seuil des pépites.
            "zone": zone,
            "zone_temoin": temoin,
            "is_favori": sv is not None,
            "favori_note": sv.note if sv else None,
            "n_photos_source": len(_photo_urls(row)),
            "photos": photos,
        })

    searches_out = [
        {
            "id": h.id, "source": h.source, "criteria": h.criteria,
            "filter_set_id": h.filter_set_id, "nb_results": h.nb_results,
            "enriched": h.enriched, "top_results": h.top_results,
            "ran_at": h.ran_at.isoformat() if h.ran_at else None,
        }
        for h in db.query(SearchHistory).order_by(SearchHistory.ran_at.desc()).all()
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "criteres": registre(),
        "sets": sets_out,
        "biens": biens_out,
        "searches": searches_out,
        "stats": {"n_biens": len(biens_out), "n_sets": len(sets_out),
                  "n_searches": len(searches_out), "n_viager": n_viager,
                  "n_residence_tourisme": n_resid, "n_mobilhome": n_mobil,
                  "n_sous_compromis": n_compromis,
                  "n_temoins_zone": n_temoins},
    }


def export_to_dir(db, out_dir: str, *, download_photos: bool = True,
                  min_match_score: float | None = None, primary_set_id: int | None = None,
                  pepites: dict | None = None, conserver: dict | None = None,
                  meilleur_par_zone: dict | None = None) -> dict:
    """Écrit out_dir/data.json (+ photos/) et renvoie les stats.

    `min_match_score`/`primary_set_id`/`pepites` : voir build_dataset (mode « pépites »).
    """
    os.makedirs(out_dir, exist_ok=True)
    data = build_dataset(db, out_dir=out_dir, download_photos=download_photos,
                         min_match_score=min_match_score, primary_set_id=primary_set_id,
                         pepites=pepites, conserver=conserver,
                         meilleur_par_zone=meilleur_par_zone)
    # Écriture ATOMIQUE : fichier temporaire puis renommage. `open(..., "w")` tronque
    # puis écrit en flux — deux exports concurrents (une collecte lancée d'un côté, un
    # ré-export de l'autre) s'entrelacent alors dans le même fichier et produisent un
    # data.json syntaxiquement invalide, donc un site qui ne charge plus. os.replace est
    # atomique sur le même système de fichiers : soit l'ancien fichier, soit le nouveau.
    final = os.path.join(out_dir, "data.json")
    tmp = f"{final}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return data["stats"]


if __name__ == "__main__":  # python -m app.services.export_static [out_dir]
    import sys

    from ..db import SessionLocal

    out = sys.argv[1] if len(sys.argv) > 1 else "../data"
    no_photos = "--no-photos" in sys.argv
    # Mode pépites optionnel, un set : EXPORT_MIN_MATCH_SCORE=78 EXPORT_PRIMARY_SET_ID=1
    # Plusieurs sets à la fois        : EXPORT_PEPITES="1:78.5,4:80"
    _mms = os.environ.get("EXPORT_MIN_MATCH_SCORE")
    _psid = os.environ.get("EXPORT_PRIMARY_SET_ID")
    min_score = float(_mms) if _mms else None
    primary = int(_psid) if _psid else None
    pepites = {}
    for morceau in (os.environ.get("EXPORT_PEPITES") or "").split(","):
        if ":" in morceau:
            sid, seuil = morceau.split(":", 1)
            pepites[int(sid.strip())] = float(seuil.strip())
    # Un témoin par zone, en plus des pépites : EXPORT_MEILLEUR_ZONE="1:70"
    # (le meilleur bien de chaque massif, s'il atteint 70 — cf. _meilleurs_par_zone).
    meilleur_zone = {}
    for morceau in (os.environ.get("EXPORT_MEILLEUR_ZONE") or "").split(","):
        if ":" in morceau:
            sid, plancher = morceau.split(":", 1)
            meilleur_zone[int(sid.strip())] = float(plancher.strip())
    # Republier un set à l'identique : EXPORT_CONSERVER="4:../data/data.json"
    conserver = {}
    for morceau in (os.environ.get("EXPORT_CONSERVER") or "").split(","):
        if ":" in morceau:
            sid, chemin = morceau.split(":", 1)
            conserver[int(sid.strip())] = _biens_publies(chemin.strip())
    stats = export_to_dir(SessionLocal(), out, download_photos=not no_photos,
                          min_match_score=min_score, primary_set_id=primary,
                          pepites=pepites or None, conserver=conserver or None,
                          meilleur_par_zone=meilleur_zone or None)
    print(f"Export -> {out}/data.json : {stats}")
