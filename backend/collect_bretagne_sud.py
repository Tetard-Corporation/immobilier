"""Crée le set « Bretagne sud » (terrain d'exception) et le peuple via Bien'ici
(seule source joignable sans navigateur/proxy).

Profil : la bonne affaire au m² plutôt que le bien qui consomme tout le budget, un
grand terrain (≥ 1 000 m²), à l'écart du pavillonnaire et du vis-à-vis, avec un coin
de nature (rivière en tête, vue dégagée, grands arbres), un logement compact allant de
la tiny house à 3-4 chambres, et le second rang de mer accepté — la vue mer se paie
trop cher. Budget ≤ 400 k€.

Zone, en deux foyers de collecte :
  - Ploemeur et le littoral morbihannais/finistérien ;
  - la vallée de la Laïta (Quimperlé, Rédené, Clohars-Carnoët, Guidel, Gestel).
Le critère `near_sea` fait le reste du travail : le long de la Laïta, il note
l'embouchure (Le Pouldu, Guidel-Plages) bien au-dessus de l'amont (Quimperlé, ~12 km),
donc « plutôt côté mer » sans exclure l'arrière-pays. Son palier `ok_km` évite de
pénaliser le second rang.

Usage :
    python collect_bretagne_sud.py            # collecte complète
    python collect_bretagne_sud.py --cap 40   # limite le nb de biens enrichis (test)
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
SET_NAME = "Bretagne sud"
SET_DESC = (
    "Bretagne sud (littoral 56-29 + vallée de la Laïta) — pépite : bonne affaire au m², "
    "grand terrain (≥ 1 000 m²), à l'écart du pavillonnaire et sans vis-à-vis, un coin de "
    "nature (rivière, vue dégagée, grands arbres), logement compact de la tiny house à "
    "3-4 chambres, second rang de mer accepté. Budget ≤ 400 k€."
)

PRIX_MAX = 400_000  # vrai plafond budgétaire

# Pondérations 1-7. Deux principes derrière ces poids :
#
# - « viser la bonne affaire » EST l'objectif du set, d'où le prix au m² à 7 : à poids égal
#   il se faisait annuler par la demi-douzaine de critères qualitatifs qu'un terrain hors
#   de prix satisfait tout aussi bien. Le budget, lui, ne fait plus que 3 : tout est
#   collecté sous 400 k€, donc il ne distingue presque rien (écart-type 0,12).
# - un critère qui n'est cité que par 4 à 15 % des annonces (sans vis-à-vis, cachet, vue)
#   ne peut servir que de bonus : personne ne peut mal noter dessus. Les intentions
#   « tranquillité » et « coin de nature » passent donc par des critères composites
#   TOUJOURS évaluables, qui montent ET descendent.
PREFERENCES = [
    {"kind": "prix_m2_terrain", "weight": 7, "label": "Prix du terrain (€/m²)", "params": {"bon": 80, "cher": 400}},
    {"kind": "tranquillite", "weight": 6, "label": "Tranquillité (ni vis-à-vis ni lotissement)", "params": {}},
    {"kind": "has_terrain", "weight": 6, "label": "Grand terrain (≥ 1 000 m²)", "params": {"min_surface": 1000}},
    {"kind": "coin_nature", "weight": 5, "label": "Coin de nature (eau, vue dégagée, arbres)", "params": {}},
    {"kind": "constructible", "weight": 5, "label": "Terrain constructible (tiny house)", "params": {}},
    # À 4, ce critère n'avait aucun effet net sur le classement (corrélation +0,05 avec la
    # note du set) : le grand terrain et le coin de nature vont de pair avec de grandes
    # maisons, et le contre-poids était trop léger. À 6 : +0,16, et 83 % des bâtis du haut
    # de tableau tiennent dans les 4 chambres (contre 77 %).
    {"kind": "logement_compact", "weight": 6, "label": "Logement compact (≤ 3-4 chambres)", "params": {"ideal": 3, "max": 4}},
    {"kind": "near_sea", "weight": 3, "label": "À portée du littoral (second rang OK)", "params": {"ok_km": 2.5, "max_km": 12}},
    {"kind": "nature_exception", "weight": 3, "label": "Nature d'exception", "params": {}},
    {"kind": "budget", "weight": 3, "label": "Budget ≤ 400 000 €", "params": {"budget_max": PRIX_MAX}},
    {"kind": "authentic", "weight": 2, "label": "Charme / cachet", "params": {}},
    {"kind": "fiber", "weight": 2, "label": "Fibre (télétravail)", "params": {}},
    {"kind": "nuisance_sonore", "weight": 2, "label": "Calme (loin autoroute/rail)", "params": {"min_m": 150, "ref_m": 800}},
    {"kind": "hiking", "weight": 1, "label": "Nature / randonnées", "params": {}},
    {"kind": "commerces", "weight": 1, "label": "Commerces/services à proximité", "params": {"ref": 15}},
]

# Foyers de collecte (libellé, centre, rayons km). Départements Morbihan (56) et
# Finistère sud (29). Le second foyer est centré sur la Laïta à mi-cours : à 12 km il
# couvre Quimperlé en amont et l'embouchure (Le Pouldu / Guidel-Plages) en aval.
ZONES = [
    ("Ploemeur / littoral", (47.735, -3.428), (8, 16, 20)),
    ("Vallée de la Laïta", (47.812, -3.535), (7, 12, 16)),
]
DEPTS = ["56", "29"]


def ensure_set(db) -> None:
    fs = db.get(FilterSet, SET_ID)
    criteria = {"property_types": ["terrain", "maison"], "preferences": PREFERENCES}
    if fs is None:
        db.add(FilterSet(id=SET_ID, name=SET_NAME, description=SET_DESC, criteria=criteria, parent_id=None))
    else:
        fs.name, fs.description, fs.criteria = SET_NAME, SET_DESC, criteria
    db.commit()
    print(f"Set « {SET_NAME} » (id {SET_ID}) prêt : {len(PREFERENCES)} préférences, terrain+maison, budget ≤ {PRIX_MAX//1000} k€.", flush=True)


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
    seen: set[str] = set()
    paniers: list[tuple[str, list]] = []  # un panier par (zone, type), dans l'ordre de priorité
    existing = {e for (e,) in db.query(Listing.external_id).filter(Listing.source == "bienici").all()}
    for zone, centre, radii in ZONES:
        for ptypes in (["terrain"], ["maison"]):
            crit = SearchCriteria(property_types=ptypes, prix_max=PRIX_MAX)
            items = src.collect_around(crit, *centre, DEPTS, radii=radii, cap=args.cap)
            panier = []
            for it in items:
                if not it.external_id or it.external_id in existing or it.external_id in seen:
                    continue
                if it.prix is not None and it.prix > PRIX_MAX:
                    continue
                seen.add(it.external_id)
                panier.append(it)
            paniers.append((f"{zone} / {ptypes[0]}", panier))
            print(f"[{zone} / {ptypes[0]}] {len(panier)} biens neufs ≤{PRIX_MAX//1000}k (sur {len(items)} annonces)", flush=True)

    # Le plafond se répartit en tourniquet entre les paniers, et non dans l'ordre de
    # collecte : à la suite, une zone abondante (les 161 maisons de Ploemeur) consommait
    # tout le quota et la Laïta n'entrait jamais dans le set — l'inverse du but.
    todo = []
    for rang in range(max((len(p) for _, p in paniers), default=0)):
        for _, panier in paniers:
            if rang < len(panier) and len(todo) < args.cap:
                todo.append(panier[rang])
        if len(todo) >= args.cap:
            break
    repartition = {}
    for nom, panier in paniers:
        pris = sum(1 for it in panier if it in todo)
        if pris:
            repartition[nom] = pris
    print(f"  plafond {args.cap} réparti : " + ", ".join(f"{k} {v}" for k, v in repartition.items()), flush=True)
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
    print(f"\nEn base : {n} biens bienici (dont Bretagne sud).", flush=True)

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
