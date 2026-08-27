"""Set « Littoral breton » (id 4) : terrain/maison d'exception en bord de mer.

Étend l'ancien set Ploemeur au littoral breton — sud (Ploemeur / Morbihan, déjà
collecté) ET nord (Côte de Granit Rose, 22 : le chaos de granite front de mer).
Profil : bord de mer / première ligne, posé dans les rochers, vue mer, nature
sauvage, constructible (tiny house) ou petite maison à rénover. Budget ≤ 400 k€.

Le sud est déjà en base (via data.json) ; ce script ajoute surtout le NORD, met à
jour les préférences (dont le critère front de mer), re-score et ré-exporte tout.

Usage : python collect_littoral.py [--cap 80]
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
from app.seed import seed_from_data_json, seed_if_empty
from app.schemas import SearchCriteria
from app.services.search import upsert_listing
from app.sources.bienici import BienIciSource

SET_ID = 4
SET_NAME = "Littoral breton"
SET_DESC = ("Terrain/maison d'exception en bord de mer breton — sud (Ploemeur/Morbihan) "
            "et Côte de Granit Rose (22). Pépite : front de mer / posé dans les rochers, "
            "vue mer, nature sauvage, constructible (tiny house) ou petite maison. ≤ 400 k€.")

PRIX_MAX = 400_000

# Pondérations 1-5. Front de mer priorisé (l'archétype : posé dans les rochers).
PREFERENCES = [
    {"kind": "budget", "weight": 5, "label": "Budget ≤ 400 000 €", "params": {"budget_max": PRIX_MAX}},
    {"kind": "feature", "weight": 5, "label": "Bord de mer / première ligne", "params": {"name": "bord_de_mer"}},
    {"kind": "distance_mer", "weight": 5, "label": "Proximité mer (distance réelle)", "params": {"proche": 300, "loin": 3000}},
    {"kind": "constructible", "weight": 5, "label": "Terrain constructible (tiny house)", "params": {}},
    {"kind": "feature", "weight": 4, "label": "Bord d'eau (rivière / étang / ria)", "params": {"name": "bord_eau"}},
    {"kind": "en_hauteur_geo", "weight": 4, "label": "Surélevé / position dominante (relief réel)", "params": {}},
    {"kind": "feature", "weight": 4, "label": "Vue (mer / dégagée)", "params": {"name": "vue"}},
    {"kind": "feature", "weight": 2, "label": "En hauteur (mention annonce)", "params": {"name": "en_hauteur"}},
    {"kind": "prix_m2_terrain", "weight": 4, "label": "Rapport qualité/prix (€/m² terrain)", "params": {"bon": 60, "cher": 300}},
    {"kind": "nature_exception", "weight": 4, "label": "Nature d'exception", "params": {}},
    {"kind": "feature", "weight": 3, "label": "Isolé / sauvage", "params": {"name": "isole"}},
    {"kind": "no_vis_a_vis", "weight": 3, "label": "Sans vis-à-vis", "params": {}},
    {"kind": "has_terrain", "weight": 3, "label": "Avec terrain", "params": {}},
    {"kind": "authentic", "weight": 3, "label": "Charme / cachet", "params": {}},
    {"kind": "hiking", "weight": 2, "label": "Sentiers côtiers / randonnées", "params": {}},
    {"kind": "nuisance_sonore", "weight": 2, "label": "Calme (loin autoroute/rail)", "params": {"min_m": 150, "ref_m": 800}},
    {"kind": "fiber", "weight": 2, "label": "Fibre (télétravail)", "params": {}},
    {"kind": "commerces", "weight": 2, "label": "Commerces/services à proximité", "params": {"ref": 15}},
]

# Pivots côtiers. Le sud (Ploemeur) est DÉJÀ largement en base -> on met le NORD
# (Côte de Granit Rose, l'archétype front-de-mer) EN PREMIER pour qu'il ne soit pas
# tronqué par le cap ; Ploemeur en dernier (complément).
PIVOTS = [
    ("Perros-Guirec / Trégastel", 48.805, -3.445, ["22"]),
    ("Plougrescant / Tréguier", 48.850, -3.230, ["22"]),
    ("Trébeurden / Lannion", 48.770, -3.560, ["22"]),
    # Baie de Morlaix : Térénez (Plougasnou) au nord, Plouezoc'h au fond de la rade,
    # Locquénolé sur l'estuaire de la Penzé. Rade abritée bordée de pointes rocheuses ;
    # `distance_mer` y sépare les biens de la rive de ceux de l'arrière-pays.
    ("Baie de Morlaix (Térénez / Plouezoc'h / Locquénolé)", 48.620, -3.840, ["29"]),
    ("Ploemeur / Morbihan", 47.735, -3.428, ["56", "29"]),
    # Second foyer morbihannais : la Laïta à mi-cours. À 12 km il couvre Quimperlé en
    # amont et l'embouchure (Le Pouldu / Guidel-Plages) en aval — `distance_mer` fait le
    # tri entre les deux, l'estuaire étant à ~0 km de mer et Quimperlé à ~12.
    ("Vallée de la Laïta", 47.812, -3.535, ["56", "29"]),
]


# --- Entonnoir -------------------------------------------------------------- #
# L'enrichissement coûte ~2,3 s par bien : sur 929 annonces remontées pour ~18 pépites,
# l'essentiel du temps partait dans des biens que le score écartait ensuite. On pré-classe
# donc sur ce que l'annonce donne GRATUITEMENT — texte et prix, aucun appel réseau — et on
# n'enrichit que le haut du panier. À ne pas confondre avec --cap, qui tronque dans l'ordre
# de collecte et garde donc le premier pivot en entier plutôt que les meilleurs biens.

# Poids alignés sur ceux du set (cf. PREFERENCES) pour que le pré-classement et le score
# final regardent dans la même direction.
_SIGNAUX_ANNONCE = {"bord_de_mer": 5.0, "bord_eau": 4.0, "vue": 4.0, "en_hauteur": 2.0}


def prescore(it) -> float:
    """Note d'annonce (0-20 environ), sans enrichissement ni réseau."""
    from app.services.export_static import _detect_equipements, _detect_pavillon_neuf

    feats = set(_detect_equipements(it.description))
    note = sum(w for k, w in _SIGNAUX_ANNONCE.items() if k in feats)

    # Pavillon neuf / lotissement viabilisé : l'inverse du bien recherché.
    if _detect_pavillon_neuf(it.description):
        note -= 4.0

    # Rapport qualité/prix du terrain, mêmes bornes que la préférence prix_m2_terrain.
    if it.surface_terrain and it.prix:
        ppm = it.prix / it.surface_terrain
        note += 3.0 if ppm <= 60 else 1.5 if ppm <= 150 else 0.0

    # Un terrain nu se prête au projet visé (tiny house) ; une grande surface aussi.
    if (it.type_bien or "").lower() == "terrain":
        note += 1.0
    if (it.surface_terrain or 0) >= 1500:
        note += 1.0

    return note


def entonnoir(items: list, keep: int) -> list:
    """Garde les `keep` meilleures annonces au pré-classement. Dit ce qu'elle écarte."""
    if keep <= 0 or len(items) <= keep:
        return items
    classees = sorted(items, key=prescore, reverse=True)
    gardes, ecartes = classees[:keep], classees[keep:]
    seuil = prescore(gardes[-1])
    print(f"Entonnoir : {len(gardes)} annonces retenues sur {len(items)} "
          f"(note ≥ {seuil:.1f}) ; {len(ecartes)} écartées sans enrichissement.", flush=True)
    return gardes


def ensure_set(db) -> None:
    fs = db.get(FilterSet, SET_ID)
    criteria = {"property_types": ["terrain", "maison"], "preferences": PREFERENCES}
    if fs is None:
        db.add(FilterSet(id=SET_ID, name=SET_NAME, description=SET_DESC, criteria=criteria, parent_id=None))
    else:
        fs.name, fs.description, fs.criteria = SET_NAME, SET_DESC, criteria
    db.commit()
    print(f"Set « {SET_NAME} » (id {SET_ID}) prêt : {len(PREFERENCES)} préférences, terrain+maison ≤{PRIX_MAX//1000}k.", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=80, help="plafond dur du nb de biens enrichis")
    ap.add_argument("--keep", type=int, default=60,
                    help="entonnoir : nb d'annonces retenues au pré-classement avant "
                         "enrichissement (0 = pas d'entonnoir)")
    ap.add_argument("--pivot", help="ne collecter qu'autour des pivots dont le nom contient "
                                    "ce texte (ex. « morlaix »)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--rescore-only", action="store_true",
                    help="ne collecte rien : met à jour le set + re-score + ré-exporte")
    ap.add_argument("--reseed", action="store_true",
                    help="RECONSTRUIT la base depuis data.json (DESTRUCTIF : efface les "
                         "biens pas encore exportés, y compris ceux d'une autre session)")
    args = ap.parse_args()

    init_db()
    # La base SQLite est partagée : un seed_from_data_json() inconditionnel efface les
    # biens qu'une autre collecte n'a pas encore exportés (cf. docs/OPERATIONS.md §0).
    if args.reseed:
        print("Reconstruction de la base depuis data.json (--reseed)...", flush=True)
        print(f"  -> {seed_from_data_json()}", flush=True)
    else:
        recap = seed_if_empty()
        print(f"Base {'seedée depuis data.json : ' + str(recap) if recap else 'déjà peuplée : seed sauté'}",
              flush=True)

    db = SessionLocal()
    ensure_set(db)

    if args.rescore_only:
        from app.services.export_static import export_to_dir
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        print("Re-score only : ré-export (détection features + scoring à jour)...", flush=True)
        t1 = time.time()
        stats = export_to_dir(db, data_dir, download_photos=True)
        print(f"  export OK en {time.time()-t1:.0f}s : {stats}", flush=True)
        db.close()
        print("TERMINÉ.", flush=True)
        return 0

    src = BienIciSource()
    existing = {e for (e,) in db.query(Listing.external_id).filter(Listing.source == "bienici").all()}
    collected: dict[str, object] = {}
    pivots = PIVOTS
    if args.pivot:
        pivots = [p for p in PIVOTS if args.pivot.lower() in p[0].lower()]
        if not pivots:
            print(f"Aucun pivot ne correspond à « {args.pivot} ». Disponibles : "
                  + ", ".join(p[0] for p in PIVOTS), flush=True)
            return 2
        print(f"Pivots retenus : {', '.join(p[0] for p in pivots)}", flush=True)
    for name, lat, lon, depts in pivots:
        for ptypes in (["terrain"], ["maison"]):
            crit = SearchCriteria(property_types=ptypes, prix_max=PRIX_MAX)
            try:
                items = src.collect_around(crit, lat, lon, depts, radii=(8, 16, 20), cap=args.cap)
            except Exception as e:  # noqa: BLE001
                print(f"  [{name}/{ptypes[0]}] collecte KO: {type(e).__name__}: {str(e)[:50]}", flush=True)
                continue
            kept = 0
            for it in items:
                if not it.external_id or it.external_id in existing or it.external_id in collected:
                    continue
                if it.prix is not None and it.prix > PRIX_MAX:
                    continue
                collected[it.external_id] = it
                kept += 1
            print(f"  [{name}/{ptypes[0]}] {kept} neufs (sur {len(items)})", flush=True)

    todo = entonnoir(list(collected.values()), args.keep)[: args.cap]
    print(f"\nEnrichissement de {len(todo)} biens neufs ({args.workers} workers)...", flush=True)
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
    print(f"\nEn base : {n} biens bienici.", flush=True)

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
