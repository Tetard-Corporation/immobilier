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
# Six critères de ce set notaient 1,00 à tout le monde (écart-type 0,00) : ceux qui LISENT
# un mot dans l'annonce. Un critère `feature` est présent ou absent — quand il est absent
# il est `n/a`, donc retiré du dénominateur, si bien qu'il ne peut QUE faire monter celui
# dont l'annonce a employé le mot. « Bord de mer » pesait 5 pour 3 % des biens, quand
# `distance_mer` mesure la même chose sur 100 % : parmi les 19 biens réellement au bord de
# l'eau, 3 seulement le disent dans leur annonce. On ne supprime pas ces critères — une
# annonce qui le dit reste un signal — mais ils passent de 5 à 1 : un mot ne doit pas
# valoir plus qu'une mesure.
PREFERENCES = [
    {"kind": "budget", "weight": 5, "label": "Budget ≤ 400 000 €", "params": {"budget_max": PRIX_MAX}},
    {"kind": "feature", "weight": 1, "label": "Bord de mer / première ligne (mention annonce)", "params": {"name": "bord_de_mer"}},
    {"kind": "distance_mer", "weight": 5, "label": "Proximité mer (distance réelle)", "params": {"proche": 300, "loin": 3000}},
    {"kind": "constructible", "weight": 5, "label": "Terrain constructible (tiny house)", "params": {}},
    # Relevé sur 0 % des biens du set : le mot n'apparaît jamais. Poids 1 en attendant
    # une mesure (le trait d'eau intérieur n'est pas encore enrichi).
    {"kind": "feature", "weight": 1, "label": "Bord d'eau (rivière / étang / ria)", "params": {"name": "bord_eau"}},
    {"kind": "en_hauteur_geo", "weight": 4, "label": "Surélevé / position dominante (relief réel)", "params": {}},
    {"kind": "feature", "weight": 2, "label": "Vue (mention annonce)", "params": {"name": "vue"}},
    # 2 % des annonces le disent ; `en_hauteur_geo` mesure la proéminence réelle sur
    # 100 % des biens, avec un écart-type de 0,31.
    {"kind": "feature", "weight": 1, "label": "En hauteur (mention annonce)", "params": {"name": "en_hauteur"}},
    {"kind": "prix_m2_terrain", "weight": 4, "label": "Rapport qualité/prix (€/m² terrain)", "params": {"bon": 60, "cher": 300}},
    {"kind": "nature_exception", "weight": 4, "label": "Nature d'exception", "params": {}},
    {"kind": "feature", "weight": 3, "label": "Isolé / sauvage", "params": {"name": "isole"}},
    # Même lecture-dans-l'annonce que les `feature` : 5 % des annonces le disent, et
    # l'absence de mention vaut n/a. À poids 3, on payait la formulation de l'agence.
    {"kind": "no_vis_a_vis", "weight": 1, "label": "Sans vis-à-vis (mention annonce)", "params": {}},
    # Sans seuil, « avec terrain » notait 1,00 dès un mètre carré : 1,00 pour les 89 %
    # de biens qui ont une surface, écart-type 0,00 — trois points de poids qui ne
    # classaient rien. 500 m² est le repère d'une parcelle où poser une tiny house avec
    # du recul ; en dessous la note descend au prorata.
    {"kind": "has_terrain", "weight": 3, "label": "Terrain (≥ 500 m²)", "params": {"min_surface": 500}},
    {"kind": "authentic", "weight": 2, "label": "Charme / cachet", "params": {}},
    # Sur le littoral, l'aléa EST le sujet : submersion marine et recul du trait de côte
    # décident de ce qu'on pourra encore construire (et assurer) dans vingt ans. Mesuré
    # depuis toujours pour le score d'investissement, jamais dans le match du set — donc
    # jamais dans ce que le groupe regarde. Poids 4, le plus haut des trois sets.
    {"kind": "risques_naturels", "weight": 4, "label": "Risques littoraux (submersion, recul du trait de côte…)", "params": {}},
    {"kind": "qualite_eau", "weight": 2, "label": "Qualité de l'eau (réseau)", "params": {}},
    # Le set mêle terrains et maisons : sur un terrain le DPE n'existe pas, le critère y
    # est simplement `n/a` — donc neutre, et non pénalisant.
    {"kind": "dpe", "weight": 2, "label": "Performance énergétique (DPE)", "params": {}},
    # Densité de sentiers et non présence : sur le littoral aussi le critère notait 1,00
    # à tout le monde. Repères plus hauts qu'en montagne — le GR34 fait grimper les
    # comptages (médiane 115 sur ce set contre 88 sur têtard).
    {"kind": "hiking", "weight": 2, "label": "Sentiers côtiers / randonnées",
     "params": {"peu": 20, "beaucoup": 250}},
    {"kind": "nuisance_sonore", "weight": 2, "label": "Calme (loin autoroute/rail)", "params": {"min_m": 150, "ref_m": 800}},
    {"kind": "fiber", "weight": 2, "label": "Fibre (télétravail)", "params": {}},
    {"kind": "commerces", "weight": 2, "label": "Commerces/services à proximité", "params": {"ref": 15}},
]

# Paliers : au-delà d'un certain score, un critère cesse d'être optionnel.
#
# `evaluate` renormalise sur les seuls critères mesurés, si bien qu'un bien dont peu de
# critères sont notés est jugé sur ceux-là — et peut monter très haut sans qu'on sache s'il
# voit l'eau. Au-dessus de 90, on exige donc une preuve : soit le contact avec l'eau est
# mesuré (mer à moins de ~1 km, subscore ≥ 0,6), soit l'annonce le dit explicitement
# (bord de mer, bord d'eau, vue). Un bien qui ne remplit rien de tout cela est ramené à 90 :
# il reste une pépite, il cesse d'être en tête.
# Les PALIERS ont été retirés le 5 septembre 2026 : ils plafonnaient un bien tant qu'une
# exigence n'était pas remplie. Mesuré sur le catalogue, ils collaient 127 biens du set
# (et 300 sous un profil « montagne ») exactement à la même valeur, et ils s'appliquaient
# à la lentille de CHACUN — personne ne pouvait donc avoir un classement vraiment
# différent. Ce qu'ils exigeaient est passé dans les critères eux-mêmes : la note budget
# tombe à zéro dès +15 % de dépassement, celle des travaux à 0,4 pour de gros travaux et
# 0,1 pour une ruine, et un critère non mesuré vaut désormais la moyenne du catalogue au
# lieu de disparaître du calcul (cf. `evaluate(..., apriori=)`).


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


def ensure_set(db) -> None:
    fs = db.get(FilterSet, SET_ID)
    criteria = {"property_types": ["terrain", "maison"], "preferences": PREFERENCES,
}
    if fs is None:
        db.add(FilterSet(id=SET_ID, name=SET_NAME, description=SET_DESC, criteria=criteria, parent_id=None))
    else:
        fs.name, fs.description, fs.criteria = SET_NAME, SET_DESC, criteria
    db.commit()
    print(f"Set « {SET_NAME} » (id {SET_ID}) prêt : {len(PREFERENCES)} préférences, terrain+maison ≤{PRIX_MAX//1000}k.", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=80, help="plafond dur du nb de biens enrichis")
    ap.add_argument("--keep", type=int, default=0,
                    help="entonnoir : plafond du nb d'annonces enrichies, appliqué au "
                         "classement par note d'annonce (0 = pas de plafond)")
    ap.add_argument("--max-km-mer", type=float, default=10.0, dest="max_km_mer",
                    help="entonnoir : écarte les communes plus éloignées de la mer "
                         "(0 = étage sauté)")
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

    from app.services.entonnoir import appliquer as entonnoir

    print(f"\nEntonnoir sur {len(collected)} annonces collectées :", flush=True)
    todo = entonnoir(list(collected.values()), max_km=args.max_km_mer,
                     garder=args.keep or None)[: args.cap]
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
