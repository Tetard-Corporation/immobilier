"""Crée le set « Ploemeur » (terrain d'exception, Bretagne sud) et le peuple via
Bien'ici (seule source joignable sans navigateur/proxy).

Profil : terrain constructible d'exception, charme/vue, nature sauvage, logement
petit accepté (tiny house). Budget illimité (pas de plafond). Zone : Ploemeur +
littoral morbihannais/finistérien (56/29).

Usage :
    python collect_ploemeur.py            # collecte complète
    python collect_ploemeur.py --cap 40   # limite le nb de biens enrichis (test)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.db import SessionLocal, init_db
from app.enrichment import enrich_listing
from app.models import FilterSet, Listing
from app.seed import seed_from_data_json
from app.schemas import SearchCriteria
from app.services.search import upsert_listing
from app.sources.bienici import BienIciSource

SET_ID = 4
SET_NAME = "Ploemeur"
SET_DESC = "Terrain d'exception, Bretagne sud (Ploemeur / littoral 56-29) — pépite : potentiel + rapport qualité/prix, charme/vue, nature sauvage, tiny-house-friendly. Budget ≤ 400 k€."

PRIX_MAX = 400_000  # vrai plafond budgétaire

# Pondérations 1-5. Cap budget + rapport qualité/prix (€/m² terrain) pour viser la pépite.
PREFERENCES = [
    {"kind": "budget", "weight": 5, "label": "Budget ≤ 400 000 €", "params": {"budget_max": PRIX_MAX}},
    {"kind": "constructible", "weight": 5, "label": "Terrain constructible (tiny house)", "params": {}},
    {"kind": "prix_m2_terrain", "weight": 4, "label": "Rapport qualité/prix (€/m² terrain)", "params": {"bon": 60, "cher": 300}},
    {"kind": "nature_exception", "weight": 4, "label": "Nature d'exception", "params": {}},
    {"kind": "feature", "weight": 4, "label": "Vue (mer / dégagée)", "params": {"name": "vue"}},
    {"kind": "feature", "weight": 3, "label": "Isolé / sauvage", "params": {"name": "isole"}},
    {"kind": "no_vis_a_vis", "weight": 3, "label": "Sans vis-à-vis", "params": {}},
    {"kind": "has_terrain", "weight": 3, "label": "Avec terrain", "params": {}},
    {"kind": "authentic", "weight": 3, "label": "Charme / cachet", "params": {}},
    {"kind": "hiking", "weight": 2, "label": "Nature / randonnées", "params": {}},
    {"kind": "nuisance_sonore", "weight": 2, "label": "Calme (loin autoroute/rail)", "params": {"min_m": 150, "ref_m": 800}},
    {"kind": "fiber", "weight": 2, "label": "Fibre (télétravail)", "params": {}},
    {"kind": "commerces", "weight": 2, "label": "Commerces/services à proximité", "params": {"ref": 15}},
]

# Ploemeur (centre) + littoral. Départements Morbihan (56) et Finistère sud (29).
PLOEMEUR = (47.735, -3.428)
DEPTS = ["56", "29"]


def ensure_set(db) -> None:
    fs = db.get(FilterSet, SET_ID)
    criteria = {"property_types": ["terrain", "maison"], "preferences": PREFERENCES}
    if fs is None:
        db.add(FilterSet(id=SET_ID, name=SET_NAME, description=SET_DESC, criteria=criteria, parent_id=None))
    else:
        fs.name, fs.description, fs.criteria = SET_NAME, SET_DESC, criteria
    db.commit()
    print(f"Set « {SET_NAME} » (id {SET_ID}) prêt : {len(PREFERENCES)} préférences, terrain+maison, budget libre.", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=70, help="nb max de biens enrichis")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    init_db()
    print("Seed DB depuis data.json...", flush=True)
    print(f"  -> {seed_from_data_json()}", flush=True)

    db = SessionLocal()
    ensure_set(db)

    src = BienIciSource()
    # terrain prioritaire, puis maison (petit logement OK). Plafond réel : 400 k€.
    collected: dict[str, object] = {}
    existing = {e for (e,) in db.query(Listing.external_id).filter(Listing.source == "bienici").all()}
    for ptypes in (["terrain"], ["maison"]):
        crit = SearchCriteria(property_types=ptypes, prix_max=PRIX_MAX)
        items = src.collect_around(crit, *PLOEMEUR, DEPTS, radii=(8, 16, 20), cap=args.cap)
        kept = 0
        for it in items:
            if not it.external_id or it.external_id in existing or it.external_id in collected:
                continue
            if it.prix is not None and it.prix > PRIX_MAX:
                continue
            collected[it.external_id] = it
            kept += 1
        print(f"[{ptypes[0]}] {kept} biens neufs ≤{PRIX_MAX//1000}k (sur {len(items)} annonces)", flush=True)

    todo = list(collected.values())[: args.cap]
    print(f"\nEnrichissement de {len(todo)} biens ({args.workers} workers)...", flush=True)
    t0 = time.time()
    enriched = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(enrich_listing, it): it for it in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                enriched.append(f.result())
            except Exception as e:  # noqa: BLE001
                print(f"  enrich KO: {type(e).__name__}: {str(e)[:60]}", flush=True)
            if i % 10 == 0:
                print(f"  {i}/{len(todo)} ({time.time()-t0:.0f}s)", flush=True)
    print(f"  enrichis: {len(enriched)} en {time.time()-t0:.0f}s", flush=True)

    for it in enriched:
        row = upsert_listing(db, it)
        row.set_ids = [SET_ID]
    db.commit()
    n = db.query(Listing).filter(Listing.source == "bienici").count()
    print(f"\nEn base : {n} biens bienici (dont Ploemeur).", flush=True)

    from app.services.export_static import export_to_dir
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    print(f"\nExport vers {os.path.abspath(data_dir)} (photos incluses)...", flush=True)
    t1 = time.time()
    stats = export_to_dir(db, data_dir, download_photos=True)
    print(f"  export OK en {time.time()-t1:.0f}s : {stats}", flush=True)
    db.close()
    print("TERMINÉ.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
