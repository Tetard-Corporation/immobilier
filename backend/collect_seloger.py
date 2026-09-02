"""Collecte SeLoger (one-shot) : SERP multi-communes -> géocodage -> enrichissement
parallèle -> scoring par set -> upsert -> export data.json + photos.

À exécuter EN LOCAL avec un cookie Datadome frais (cf. docs/OPERATIONS.md §2 et
scripts/datadome_cookies.py) : sans lui la source se déclare indisponible.

SeLoger n'indexe qu'à la commune et n'accepte que ses identifiants internes
(`AD08FR<n>`), pas les codes postaux. On aligne donc les zones sur celles de
`collect_leboncoin.py` en résolvant, pour chaque code postal, la commune la plus
peuplée -> son `placeId` (mis en cache dans data/seloger_places.json).

Usage :
    python collect_seloger.py --zone ploemeur      # une zone
    python collect_seloger.py                      # toutes les zones
    python collect_seloger.py --dry-run            # collecte seule, rien écrit
    python collect_seloger.py --no-export          # upsert sans ré-export data.json
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.db import SessionLocal, init_db
from app.enrichment import enrich_listing
from app.models import Listing
from app.schemas import SearchCriteria
from app.seed import seed_from_data_json, seed_if_empty
from app.services.geo_communes import communes_for_postcode
from app.services.search import upsert_listing
from app.sources.scraper import ScraperBlocked
from app.sources.seloger import ABSENT, SeLogerSource
from collect_leboncoin import PLOEMEUR_ZIPS, TETARD_ZIPS

# Mêmes zones que la collecte Leboncoin, pour que les sets restent comparables.
# `entonnoir` : les paramètres passés à services.entonnoir.appliquer, qui remplace le
# plafond aveugle d'avant (cf. plus bas, dans main).
ZONES = {
    # Plafond aligné sur collect_tetard.PRIX_MAX (250 k€ depuis le 30 août).
    "tetard": {"zips": TETARD_ZIPS, "set_ids": [1, 2], "types": ["maison"],
               "prix_max": 250000, "pages": 10,
               "entonnoir": {"profil": "montagne", "min_altitude": 250.0,
                             "prix_max": 250000, "garder": 900}},
    # Terrains ≤400k ET maisons ≤400k AVEC terrain (longères/pépites à rénover).
    "ploemeur": {"zips": PLOEMEUR_ZIPS, "set_ids": [4], "types": ["terrain", "maison"],
                 "prix_max": 400000, "pages": 6, "min_terrain_maison": 300,
                 "entonnoir": {"profil": "littoral", "max_km": 10.0, "garder": 200}},
}

# SeLoger fusionne les résultats de plusieurs communes en une seule requête. C'est
# économique, mais c'est aussi un plafond invisible : la SERP est paginée GLOBALEMENT,
# donc les communes d'un lot se partagent `pages` × ~20 résultats. À quinze communes par
# lot, La Table était interrogée avec Albertville et Saint-Jean-de-Maurienne — les bourgs
# saturaient les pages, les hameaux ne remontaient jamais. Et comme la requête ne changeait
# pas d'un run à l'autre, relancer ne servait à rien. La composition des lots est
# maintenant décidée par `_lots`, au poids de population et non au nombre.


def resolve_places(src: SeLogerSource, zips: list[str]) -> tuple[list[dict], dict]:
    """Résout les codes postaux d'une zone en placeIds SeLoger (cache disque).

    **Toutes** les communes de chaque code postal, et non la seule plus peuplée. C'est le
    défaut qui a coûté le plus cher : un code postal rural en couvre jusqu'à quatorze
    (73110 = Valgelon-La Rochette, La Table, Le Pontet, Arvillard…), et n'en interroger
    qu'une rendait les treize autres **définitivement** invisibles — aucune relance n'y
    changeait rien, puisque la requête ne les nommait pas. Mesuré sur la zone têtard :
    207 communes interrogées sur 1 324, soit 15 % du territoire.

    Le cas aggravant : quand la commune la plus peuplée est une *commune nouvelle* que
    SeLoger n'indexe pas encore (Valgelon-La Rochette, Grand-Aigueblanche), elle n'a pas
    de placeId — et c'est le code postal ENTIER qui disparaissait de la collecte.

    Renvoie ([{place_id, nom, population}], {commune: {lat, lon, code}}) : les cartes de
    la SERP ne portant pas de coordonnées, on réutilise les centroïdes de commune."""
    places: list[dict] = []
    centres: dict[str, dict] = {}
    vus: set[str] = set()
    sans_id: list[str] = []
    budget = _BUDGET_RESOLUTION
    epuise = False
    for cp in zips:
        communes = communes_for_postcode(cp)
        if not communes:
            print(f"  {cp} : commune introuvable", flush=True)
            continue
        for commune in communes:
            nom = commune.get("nom")
            if not nom or nom in vus:
                continue
            vus.add(nom)
            if commune.get("lat") is not None:
                centres[nom] = {"lat": commune["lat"], "lon": commune["lon"],
                                "code": commune["code"]}
            deja = src.place_id_en_cache(nom, cp[:2])
            if deja is ABSENT:
                if epuise or budget <= 0:
                    epuise = True
                    continue          # hors budget : ce sera pour la prochaine passe
                budget -= 1
                try:
                    pid = src.place_id(nom, cp[:2])
                except ScraperBlocked as e:
                    # Ne PAS continuer : sans cookie valide, chaque commune suivante
                    # serait comptée « sans placeId » alors qu'on est simplement bloqué.
                    print(f"\n  BLOQUÉ à la résolution ({nom}) : {str(e)[:70]}", flush=True)
                    print("  cookie à régénérer : python scripts/datadome_cookies.py "
                          "--site seloger", flush=True)
                    print(f"  {len(places)} communes résolues avant le blocage — elles "
                          "sont en cache, la relance repart de là.", flush=True)
                    epuise = True
                    continue
            else:
                pid = deja
            if pid:
                places.append({"place_id": pid, "nom": nom,
                               "population": commune.get("population") or 0})
            else:
                # `pid` vaut None uniquement après une réponse de SeLoger, jamais après
                # un blocage (qui fait `continue`) ni hors budget : cette commune n'est
                # réellement pas indexée.
                sans_id.append(f"{nom} ({cp})")
    if sans_id:
        print(f"  {len(sans_id)} commune(s) que SeLoger n'indexe pas (vérifié, en cache) : "
              f"{', '.join(sans_id[:8])}{'…' if len(sans_id) > 8 else ''}", flush=True)
    if epuise:
        restantes = len(vus) - len(places) - len(sans_id)
        print(f"\n  ⚠ RÉSOLUTION INCOMPLÈTE : ~{max(restantes, 0)} commune(s) pas encore "
              f"résolues. Relancer pour en faire une tranche de plus (le cache est "
              f"permanent, rien n'est redemandé deux fois).", flush=True)
    return places, centres


# Nombre de communes NOUVELLES résolues par passage. Chaque résolution est une requête
# SeLoger, et `docs/OPERATIONS.md` mesure qu'environ 200 requêtes passent avant que le
# cookie Datadome ne se brûle. Une passe unique sur les ~1 300 communes de la zone têtard
# a tenu 175 communes puis a tout perdu : les 53 lots suivants ont été refusés, la
# collecte a ramené zéro bien. Le cache des placeIds étant permanent, on résout donc par
# tranches — cinq à sept passages couvrent la zone, et elle est ensuite acquise.
_BUDGET_RESOLUTION = int(os.environ.get("SELOGER_BUDGET_RESOLUTION", "150"))

# Budget de population par lot. La SERP étant paginée GLOBALEMENT pour toutes les communes
# d'une requête, un lot dispose de `pages` × ~20 résultats à partager. Grouper au nombre de
# communes mettait Chambéry avec des hameaux : la ville saturait les pages, les hameaux ne
# remontaient jamais. On groupe donc au poids : un lot se ferme dès que la population
# cumulée dépasse ce seuil, ou qu'il atteint `_MAX_PLACES_PAR_LOT` communes.
_POP_PAR_LOT = int(os.environ.get("SELOGER_POP_PAR_LOT", "12000"))
_MAX_PLACES_PAR_LOT = int(os.environ.get("SELOGER_MAX_PLACES_PAR_LOT", "12"))


def _lots(places: list[dict]) -> list[list[dict]]:
    """Groupe les communes en lots homogènes : les grosses seules, les petites ensemble."""
    lots: list[list[dict]] = []
    courant: list[dict] = []
    pop = 0
    for pl in sorted(places, key=lambda p: -p["population"]):
        seul = pl["population"] >= _POP_PAR_LOT
        if courant and (seul or pop + pl["population"] > _POP_PAR_LOT
                        or len(courant) >= _MAX_PLACES_PAR_LOT):
            lots.append(courant)
            courant, pop = [], 0
        courant.append(pl)
        pop += pl["population"]
        if seul:                      # une ville occupe tout un lot : elle a besoin des
            lots.append(courant)      # dix pages pour elle seule.
            courant, pop = [], 0
    if courant:
        lots.append(courant)
    return lots


def collect_zone(src: SeLogerSource, name: str, zone: dict) -> tuple[list, list[int]]:
    """Toutes les pages d'une zone, dédupliquées et filtrées (prix, type, terrain).

    Renvoie (biens, lots_non_interrogés). Le second terme est le point important : un lot
    que Datadome a coupé ressemble trait pour trait à un lot qui n'avait rien à donner.
    L'ancienne version rendait la main en silence dès le premier blocage, abandonnant tous
    les lots suivants — c'est ainsi que la Savoie s'est retrouvée à 50 biens sans que rien
    ne le signale. On note désormais ce qui n'a pas été vu, et l'appelant refuse de
    conclure sur une collecte incomplète.
    """
    print(f"\n[{name}] résolution des {len(zone['zips'])} codes postaux "
          f"(toutes leurs communes)...", flush=True)
    places, centres = resolve_places(src, zone["zips"])
    print(f"[{name}] {len(places)} communes résolues", flush=True)
    if not places:
        return [], []

    crit = SearchCriteria(property_types=zone["types"], prix_max=zone["prix_max"])
    seen: dict[str, object] = {}
    lots = _lots(places)
    chunks = [[pl["place_id"] for pl in lot] for lot in lots]
    print(f"[{name}] {len(chunks)} lots (population par lot ≤ {_POP_PAR_LOT}, "
          f"≤ {_MAX_PLACES_PAR_LOT} communes)", flush=True)
    manques: list[int] = []
    bloque = False
    for ci, chunk in enumerate(chunks, 1):
        if bloque:
            manques.append(ci)
            continue
        for page in range(1, zone.get("pages", 3) + 1):
            try:
                items = src.search_place(crit, chunk, page=page)
            except ScraperBlocked as e:
                # Un blocage n'est pas une fin de zone : on arrête d'interroger, mais on
                # RETIENT les lots qu'on n'aura pas vus, pour que le récapitulatif le dise.
                print(f"[{name}] BLOQUÉ (lot {ci}, page {page}) : {str(e)[:70]}", flush=True)
                print("       cookie à régénérer : python scripts/datadome_cookies.py", flush=True)
                bloque = True
                manques.append(ci)
                break
            except Exception as e:  # noqa: BLE001
                print(f"[{name}] lot {ci} page {page} KO : {type(e).__name__}: {str(e)[:60]}", flush=True)
                manques.append(ci)
                break
            if not items:
                break
            new = 0
            for it in items:
                if not it.external_id or it.external_id in seen:
                    continue
                if it.prix is None or it.prix > zone["prix_max"]:
                    continue
                min_terr = zone.get("min_terrain_maison")
                if min_terr and it.type_bien == "maison" and (it.surface_terrain or 0) < min_terr:
                    continue
                centre = centres.get(it.commune or "")
                if centre:
                    it.latitude, it.longitude = centre["lat"], centre["lon"]
                    it.code_commune = it.code_commune or centre["code"]
                seen[it.external_id] = it
                new += 1
            print(f"[{name}] lot {ci}/{len(chunks)} p{page} : {len(items)} cartes, "
                  f"{new} retenues (cumul {len(seen)})", flush=True)
    if manques:
        perdues = sum(len(lots[i - 1]) for i in manques if 0 < i <= len(lots))
        print(f"[{name}] ⚠ {len(manques)} lot(s) sur {len(chunks)} NON interrogés "
              f"({perdues} communes) : la collecte de cette zone est INCOMPLÈTE.",
              flush=True)
    return list(seen.values()), sorted(set(manques))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone", choices=sorted(ZONES), action="append",
                    help="zone(s) a collecter (defaut : toutes)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true", help="collecte seule, rien ecrit")
    ap.add_argument("--no-export", action="store_true", help="upsert sans re-export data.json")
    ap.add_argument("--reseed", action="store_true",
                    help="RECONSTRUIT la base depuis data.json (DESTRUCTIF : efface les "
                         "biens non encore exportes, y compris ceux d'une autre session)")
    args = ap.parse_args()

    src = SeLogerSource()
    if not src.available:
        print("SeLoger indisponible : ni PROXY_URL ni SELOGER_DATADOME.", flush=True)
        print("  -> python scripts/datadome_cookies.py --site seloger", flush=True)
        return 1

    zones = {k: ZONES[k] for k in (args.zone or sorted(ZONES))}

    if args.dry_run:
        for name, z in zones.items():
            items, _manques = collect_zone(src, name, z)
            print(f"\n[{name}] {len(items)} biens collectes (dry-run)")
            for it in items[:12]:
                print(f"   {str(it.type_bien):9s} {str(it.prix):>9s} EUR  bati={str(it.surface_bati):>7s} "
                      f"terrain={str(it.surface_terrain):>8s}  {str(it.commune):18s} {it.url}")
        return 0

    init_db()
    # La base SQLite est un store de travail PARTAGE : une autre session (ou une autre
    # collecte) peut y avoir des biens pas encore exportes vers data.json. Un
    # seed_from_data_json() inconditionnel les EFFACE — vécu, 200 biens perdus. On ne
    # seede donc que si la base est vide, et le mode destructif est explicite.
    if args.reseed:
        print("Reconstruction de la base depuis data.json (--reseed)...", flush=True)
        print(f"  -> {seed_from_data_json()}", flush=True)
    else:
        recap = seed_if_empty()
        print(f"Base {'seedee depuis data.json : ' + str(recap) if recap else 'deja peuplee : seed saute'}",
              flush=True)
    db = SessionLocal()
    existing = {e for (e,) in db.query(Listing.external_id).filter(Listing.source == "seloger").all()}

    from app.services.entonnoir import appliquer as entonnoir

    collected: dict[str, tuple] = {}
    incomplet: dict[str, int] = {}
    for name, z in zones.items():
        items, manques = collect_zone(src, name, z)
        if manques:
            incomplet[name] = len(manques)
        neufs = [it for it in items
                 if it.external_id and it.external_id not in existing
                 and it.external_id not in collected]
        print(f"\n[{name}] {len(neufs)} biens neufs sur {len(items)} collectés.", flush=True)
        # L'ancienne version coupait ici à `target`, dans l'ordre des lots — c'est-à-dire
        # par département, l'Isère d'abord. Un plafond appliqué AVANT tout classement
        # n'est pas un tri : il gardait les 300 premiers arrivés, pas les 300 meilleurs,
        # et les derniers lots (Alpes-Maritimes, Alpes-de-Haute-Provence) n'entraient
        # jamais. L'entonnoir, lui, écarte sur des critères mesurés — et il est déjà ce
        # que `collect_tetard.py` et `collect_littoral.py` utilisent.
        if neufs:
            print(f"[{name}] entonnoir :", flush=True)
            neufs = entonnoir(neufs, **z["entonnoir"])
        for it in neufs:
            collected[it.external_id] = (it, z["set_ids"])

    todo = list(collected.values())
    print(f"\nEnrichissement de {len(todo)} biens ({args.workers} workers)...", flush=True)

    def work(pair):
        item, set_ids = pair
        return enrich_listing(item), set_ids

    t0 = time.time()
    enriched = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, p): p for p in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                enriched.append(f.result())
            except Exception as e:  # noqa: BLE001
                print(f"  enrich KO: {type(e).__name__}: {str(e)[:60]}", flush=True)
            if i % 10 == 0:
                print(f"  {i}/{len(todo)} ({time.time()-t0:.0f}s)", flush=True)
    print(f"  enrichis: {len(enriched)} en {time.time()-t0:.0f}s", flush=True)

    for item, set_ids in enriched:
        row = upsert_listing(db, item)
        row.set_ids = set_ids
    db.commit()
    n = db.query(Listing).filter(Listing.source == "seloger").count()
    print(f"\nEn base : {n} biens seloger.", flush=True)

    # Un export réécrit data.json ET retélécharge les photos : la dernière passe a
    # tourné 9 h 55 pour republier à l'identique, alors que la collecte avait été
    # entièrement bloquée et n'avait ramené aucun bien. Rien de neuf, rien à exporter.
    if not enriched:
        print("\nAucun bien neuf : export sauté (il ne republierait que l'existant).",
              flush=True)
    elif not args.no_export:
        from app.services.export_static import export_to_dir
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        print(f"\nExport vers {os.path.abspath(data_dir)} (photos incluses)...", flush=True)
        t1 = time.time()
        stats = export_to_dir(db, data_dir, download_photos=True)
        print(f"  export OK en {time.time()-t1:.0f}s : {stats}", flush=True)
    db.close()
    if incomplet:
        detail = ", ".join(f"{n} ({k} lot(s))" for n, k in incomplet.items())
        print(f"\n⚠ COLLECTE INCOMPLÈTE — zone(s) : {detail}.", flush=True)
        print("  Régénérer le cookie puis relancer : les biens déjà en base sont sautés,", flush=True)
        print("  la relance reprend donc là où celle-ci s'est arrêtée.", flush=True)
        print("  (python scripts/datadome_cookies.py --site seloger)", flush=True)
        return 2
    print("TERMINE.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
