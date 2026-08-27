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
_OVERPASS = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")


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
    # Une seule requête : autoroute/voie ferrée (bruit) + sentiers/itinéraires (rando).
    q = (f'[out:json][timeout:25];('
         f'way(around:2500,{lat},{lon})[highway~"motorway|trunk"];'
         f'way(around:2500,{lat},{lon})[railway=rail];'
         f'way(around:1500,{lat},{lon})[highway~"path|footway|bridleway"];'
         f'relation(around:3000,{lat},{lon})[route=hiking];);out geom 300;')
    try:
        data = urllib.parse.urlencode({"data": q}).encode()
        req = urllib.request.Request(_OVERPASS, data=data, headers={"User-Agent": _UA_OVERPASS})
        with urllib.request.urlopen(req, timeout=40) as r:
            payload = json.loads(r.read())
    except Exception:
        return None
    best = {"dist_autoroute_m": None, "dist_rail_m": None, "infra_checked": True}
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
        key = "dist_autoroute_m" if hw in ("motorway", "trunk") else ("dist_rail_m" if tags.get("railway") == "rail" else None)
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
        self.latitude = row.latitude
        self.longitude = row.longitude
        self.flags = {c: getattr(row, c) for c in _FLAG_COLS}
        if extra_flags:
            self.flags.update(extra_flags)


def _pref_dump(pref) -> dict:
    if isinstance(pref, dict):
        return {"kind": pref.get("kind"), "label": pref.get("label") or pref.get("kind"),
                "weight": pref.get("weight", 1.0), "params": pref.get("params") or {}}
    return {"kind": getattr(pref, "kind", None), "label": getattr(pref, "label", None),
            "weight": getattr(pref, "weight", 1.0), "params": getattr(pref, "params", {}) or {}}


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


def _passes_pepites_gate(scores_by_set: dict, member: set, primary_set_id: int, min_score: float) -> bool:
    """Filtre « pépites » : ne garde un bien du set primaire que si son match_score y est
    ≥ seuil. Un bien qui n'appartient PAS au set primaire (ex. Pauline vs têtard) est
    toujours conservé — le gate ne concerne que la recherche resserrée d'un set donné.
    """
    if member and primary_set_id not in member:
        return True  # hors du set primaire -> non concerné par le resserrage
    sc = (scores_by_set.get(str(primary_set_id)) or {}).get("match_score")
    return isinstance(sc, (int, float)) and sc >= min_score


def build_dataset(db, *, out_dir: str | None = None, download_photos: bool = False,
                  min_match_score: float | None = None, primary_set_id: int | None = None) -> dict:
    """Construit le dataset statique. Si download_photos, écrit les images sous out_dir.

    Mode « pépites » (optionnel) : si `min_match_score` est fourni, ne conserve dans
    l'export que les biens du `primary_set_id` dont le match_score y est ≥ seuil (les
    biens des autres sets sont préservés). Sert à resserrer une recherche sur le haut
    du panier sans toucher aux autres sets partageant le dataset.
    """
    sets = (
        db.query(FilterSet)
        .order_by(FilterSet.parent_id.isnot(None), FilterSet.id)
        .all()
    )
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
    n_viager = 0
    n_resid = 0
    infra_cache = _load_infra_cache()
    poi_cache = _load_poi_cache()
    relief_cache = _load_relief_cache()
    sea_cache = _load_sea_cache()
    fibre_lut = _load_fibre_lut()
    tension_lut = _load_tension_lut()
    rows = (
        db.query(Listing)
        .filter(Listing.source != "mock")
        .order_by(Listing.score.isnot(None).desc(), Listing.score.desc())
        .all()
    )
    for row in rows:
        # Types conservés mais fortement déclassés (renvoyés en fond de classement) :
        # viager/nue-propriété (prix = bouquet, occupé) et résidence de tourisme sous
        # bail commercial (jouissance restreinte, gestion imposée, revente difficile).
        is_viager = _detect_viager(row.description, row.adresse)
        is_resid = _detect_residence_tourisme(row.description, row.adresse)
        if is_viager:
            n_viager += 1
        if is_resid:
            n_resid += 1
        if is_viager:
            penalty = (_VIAGER_MATCH_FACTOR, "Viager / nue-propriété",
                       "viager (prix = bouquet, bien occupé) — fortement déclassé")
        elif is_resid:
            penalty = (_RESID_MATCH_FACTOR, "Résidence de tourisme / bail commercial",
                       "résidence de tourisme (bail commercial, gestion imposée) — fortement déclassé")
        else:
            penalty = None
        infra = _infra_distances(row.latitude, row.longitude, infra_cache)
        poi = _poi_distances(row.latitude, row.longitude, poi_cache)
        relief = _relief_prominence(row.latitude, row.longitude, relief_cache)
        sea = _sea_distance(row.latitude, row.longitude, sea_cache)  # cache-only (réchauffé à part)
        feats = list(row.features or [])
        for e in _detect_equipements(row.description):
            if e not in feats:
                feats.append(e)
        extra = {**infra, **poi, **relief, **sea, **_fibre_flags(row.code_commune, fibre_lut),
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
        scores_by_set = {}
        for fs_id, prefs in set_prefs.items():
            if not prefs:
                continue
            if member and fs_id not in member:
                continue  # bien hors de ce set (ex. montagne vs Pauline) -> pas de score
            match, details = evaluate(item, prefs, set_exigences.get(fs_id))
            if penalty and match is not None:
                # Pénalité forte : ce type plafonne très bas quelles que soient ses qualités.
                factor, plabel, pdetail = penalty
                match = round(match * factor, 1)
                details.insert(0, {"kind": "disqualifiant", "label": plabel,
                                   "weight": 0, "status": "ko", "subscore": 0, "detail": pdetail})
            scores_by_set[str(fs_id)] = {"match_score": match, "details": details}

        # Mode pépites : on saute les biens du set primaire sous le seuil (autres sets gardés).
        if min_match_score is not None and primary_set_id is not None:
            if not _passes_pepites_gate(scores_by_set, member, primary_set_id, min_match_score):
                continue

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
        "sets": sets_out,
        "biens": biens_out,
        "searches": searches_out,
        "stats": {"n_biens": len(biens_out), "n_sets": len(sets_out),
                  "n_searches": len(searches_out), "n_viager": n_viager, "n_residence_tourisme": n_resid},
    }


def export_to_dir(db, out_dir: str, *, download_photos: bool = True,
                  min_match_score: float | None = None, primary_set_id: int | None = None) -> dict:
    """Écrit out_dir/data.json (+ photos/) et renvoie les stats.

    `min_match_score`/`primary_set_id` : voir build_dataset (mode « pépites »).
    """
    os.makedirs(out_dir, exist_ok=True)
    data = build_dataset(db, out_dir=out_dir, download_photos=download_photos,
                         min_match_score=min_match_score, primary_set_id=primary_set_id)
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
    # Mode pépites optionnel : EXPORT_MIN_MATCH_SCORE=78 EXPORT_PRIMARY_SET_ID=1
    _mms = os.environ.get("EXPORT_MIN_MATCH_SCORE")
    _psid = os.environ.get("EXPORT_PRIMARY_SET_ID")
    min_score = float(_mms) if _mms else None
    primary = int(_psid) if _psid else None
    stats = export_to_dir(SessionLocal(), out, download_photos=not no_photos,
                          min_match_score=min_score, primary_set_id=primary)
    print(f"Export -> {out}/data.json : {stats}")
