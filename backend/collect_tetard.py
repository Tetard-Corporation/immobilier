"""Set « têtard » (id 1) et son sous-set « Léo » (id 2) : maison de retrait entre copains.

Refonte des critères + collecte, dans l'esprit de ce qui a été fait sur le littoral
breton. Ce que le groupe a tranché, et ce que le score en fait :

- « 3/4 chambres » -> capacité d'accueil mesurée sur TOUTES les annonces (repli sur les
  pièces, puis sur la surface) et exigée au-dessus de 75. Sans ce repli le critère était
  `n/a` sur la moitié du lot, donc neutre : une maison d'une seule pièce était deuxième.
- « le rapport qualité/prix c'est essentiel » -> nouveau critère au poids le plus fort :
  le prix au m² du bien contre celui des ventes du secteur (DVF).
- « l'accès à la nature/montagne grand OUI, mais pas isolé » -> nature et relief au poids
  fort, isolement retiré du critère de tranquillité, commerces/services remontés.
- « pas le bâti ancien, ça veut dire travaux » -> « peu de travaux » monte à 4, le critère
  « authentique / cachet » sort (il ne servait que de bonus : 1,00 quand cité, n/a sinon).
- budget ramené de 600 k€ à 450 k€.

Usage :
    python collect_tetard.py --rescore-only     # met à jour les sets, re-score, ré-exporte
    python collect_tetard.py                    # collecte bienici autour des pivots + tout ça
    python collect_tetard.py --pivot diois      # un seul pivot
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.db import SessionLocal, init_db
from app.enrichment import enrich_listing
from app.models import FilterSet, Listing
from app.schemas import SearchCriteria
from app.seed import seed_from_data_json, seed_if_empty
from app.services.search import upsert_listing
from app.sources.bienici import BienIciSource

SET_ID = 1
SET_NAME = "têtard"
SET_DESC = ("Maison de retrait entre copains — Drôme / Ardèche / Savoie / Ain, à moins de "
            "4h porte-à-porte de Paris. Pépite : bon rapport qualité/prix, 3 chambres ou "
            "plus, peu de travaux, la montagne et la nature à la porte — mais un village "
            "vivant autour, pas le bout du monde. ≤ 300 k€.")

SOUS_SET_ID = 2
SOUS_SET_NAME = "Léo"
SOUS_SET_DESC = "Préférences perso de Léo : isolement assumé, grand terrain, vue panoramique."

PRIX_MAX = 300_000

# Pondérations 1-5. Deux critères mènent le classement : ce que le bien vaut pour son prix,
# et ce qu'on a devant la porte.
PREFERENCES = [
    {"kind": "rapport_qualite_prix", "weight": 5, "label": "Rapport qualité/prix (vs prix du secteur)",
     "params": {"bon": 0.75, "cher": 1.7}},
    {"kind": "budget", "weight": 4, "label": "Budget ≤ 300 000 €", "params": {"budget_max": PRIX_MAX}},
    {"kind": "chambres_min", "weight": 4, "label": "3 chambres minimum", "params": {"min": 3}},
    {"kind": "light_works", "weight": 4, "label": "Peu de travaux", "params": {}},
    {"kind": "coin_nature", "weight": 4, "label": "Coin de nature (eau, bois, vue dégagée)",
     "params": {"alt_min": 400, "alt_ref": 900}},
    {"kind": "relief_mountain", "weight": 4, "label": "Montagne / relief", "params": {"ref_altitude": 800}},
    {"kind": "commerces", "weight": 3, "label": "Village vivant (commerces, services)", "params": {"ref": 15}},
    {"kind": "hiking", "weight": 3, "label": "Randonnées au départ", "params": {}},
    {"kind": "temps_acces", "weight": 3, "label": "≤ 4h porte-à-porte depuis Paris",
     "params": {"max_minutes": 240}},
    # Isolement neutralisé : le groupe veut le calme, pas le bout du monde.
    {"kind": "tranquillite", "weight": 3, "label": "Calme, sans vis-à-vis, hors lotissement",
     "params": {"poids_isolement": 0, "poids_densite": 0}},
    {"kind": "has_terrain", "weight": 3, "label": "Terrain ≥ 1 000 m²", "params": {"min_surface": 1000}},
    {"kind": "surface_habitable", "weight": 2, "label": "≥ 100 m² habitables", "params": {"min": 100}},
    {"kind": "near_gare", "weight": 2, "label": "Proche d'une gare", "params": {"max_km": 15}},
    {"kind": "nuisance_sonore", "weight": 2, "label": "Loin d'une autoroute / voie ferrée",
     "params": {"min_m": 200, "ref_m": 1000}},
    {"kind": "fiber", "weight": 2, "label": "Fibre (télétravail)", "params": {}},
    {"kind": "ski", "weight": 2, "label": "Station de ski à proximité", "params": {"max_km": 30}},
    {"kind": "near_city", "weight": 2, "label": "Accessible depuis Marseille",
     "params": {"ville": "Marseille", "max_km": 300}},
    {"kind": "near_corridor", "weight": 1, "label": "Axe Paris-Marseille",
     "params": {"villes": ["Paris", "Marseille"], "max_km": 40}},
]

# Paliers : au-delà d'un certain score, un critère cesse d'être optionnel.
#
# `evaluate` renormalise sur les seuls critères mesurés. Un bien dont peu de critères sont
# notés est donc jugé sur ceux-là, et peut monter très haut sans qu'on sache combien de
# monde il loge. C'est exactement ce qui s'est produit : une annonce d'une seule pièce
# deuxième d'un classement qui demandait quatre chambres.
EXIGENCES = [
    {
        "above": 75,
        "label": "Capacité d'accueil prouvée (requis au-dessus de 75)",
        "requires": ["chambres_min"],
        "mode": "all",
        "min_subscore": 0.99,  # = le minimum de chambres atteint, estimation comprise
    },
    {
        # Une pépite est une bonne affaire PROUVÉE. Sans surface bâtie il n'y a pas de prix
        # au m², donc rien à comparer au secteur — et le bien monte précisément parce qu'il
        # est peu mesuré. Le palier ferme cette porte.
        "above": 78,
        "label": "Rapport qualité/prix mesuré (requis au-dessus de 78)",
        "requires": ["rapport_qualite_prix"],
        "mode": "all",
        "min_subscore": 0.5,
    },
    {
        "above": 85,
        "label": "Nature ou montagne avérée (requis au-dessus de 85)",
        "requires": ["coin_nature", "relief_mountain", "hiking"],
        "mode": "any",
        "min_subscore": 0.6,
    },
]

# Sous-set « Léo » : seules les DIFFÉRENCES avec le parent (fusion par `kind`).
PREFERENCES_LEO = [
    {"kind": "relief_mountain", "weight": 5, "label": "Montagne / relief", "params": {"ref_altitude": 800}},
    {"kind": "has_terrain", "weight": 4, "label": "Grand terrain (≥ 1 500 m²)", "params": {"min_surface": 1500}},
    # Léo, lui, veut l'isolement : poids par défaut du critère.
    {"kind": "tranquillite", "weight": 4, "label": "Isolé, au calme, sans vis-à-vis", "params": {}},
    {"kind": "feature", "weight": 3, "label": "Vue panoramique", "params": {"name": "vue_panoramique"}},
]

# Pivots de collecte : les parties MONTAGNE des départements déjà couverts par le set
# (26, 07, 73, 01, 43, 42), toutes à moins de 4h porte-à-porte de Paris. La zone ne
# change pas ; ce sont les points de départ qui quittent la vallée du Rhône, d'où venait
# la majorité du haut de classement précédent (Châteauneuf-sur-Isère, 154 m).
PIVOTS = [
    ("Diois / Die", 44.754, 5.370, ["26"]),
    ("Vercors drômois / La Chapelle-en-Vercors", 44.968, 5.415, ["26"]),
    ("Haut-Vivarais / Lamastre", 44.985, 4.580, ["07"]),
    ("Cévennes ardéchoises / Antraigues-Aubenas", 44.730, 4.390, ["07"]),
    ("Bauges / Le Châtelard", 45.700, 6.110, ["73"]),
    ("Bugey / Hauteville-Lompnes", 45.980, 5.600, ["01"]),
    ("Mézenc / Le Monastier-sur-Gazeille", 44.940, 4.000, ["43"]),
    ("Pilat / Bourg-Argental", 45.330, 4.560, ["42", "07"]),
]


def ensure_sets(db) -> None:
    criteria = {"property_types": ["maison"], "preferences": PREFERENCES, "exigences": EXIGENCES}
    fs = db.get(FilterSet, SET_ID)
    if fs is None:
        db.add(FilterSet(id=SET_ID, name=SET_NAME, description=SET_DESC, criteria=criteria))
    else:
        fs.name, fs.description, fs.criteria = SET_NAME, SET_DESC, criteria

    # Le sous-set ne porte que ses différences : le reste (dont les paliers) est hérité.
    sous = db.get(FilterSet, SOUS_SET_ID)
    criteria_leo = {"preferences": PREFERENCES_LEO}
    if sous is None:
        db.add(FilterSet(id=SOUS_SET_ID, name=SOUS_SET_NAME, description=SOUS_SET_DESC,
                         criteria=criteria_leo, parent_id=SET_ID))
    else:
        sous.name, sous.description, sous.criteria, sous.parent_id = (
            SOUS_SET_NAME, SOUS_SET_DESC, criteria_leo, SET_ID)
    db.commit()
    print(f"Sets prêts : « {SET_NAME} » ({len(PREFERENCES)} critères, {len(EXIGENCES)} paliers, "
          f"≤{PRIX_MAX // 1000}k) et « {SOUS_SET_NAME} » ({len(PREFERENCES_LEO)} surcharges).",
          flush=True)


def _seuils(texte: str | None) -> dict:
    """« 1:78.5,4:80 » -> {1: 78.5, 4: 80.0}."""
    out = {}
    for morceau in (texte or "").split(","):
        if ":" in morceau:
            sid, seuil = morceau.split(":", 1)
            out[int(sid.strip())] = float(seuil.strip())
    return out


def _exporter(db, quoi: str, pepites: dict | None = None) -> None:
    from app.services.export_static import export_to_dir

    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    print(f"\n{quoi} vers {os.path.abspath(data_dir)}...", flush=True)
    if pepites:
        print(f"  resserrage : {', '.join(f'set {k} ≥ {v:g}' for k, v in pepites.items())}", flush=True)
    t = time.time()
    stats = export_to_dir(db, data_dir, download_photos=True, pepites=pepites or None)
    print(f"  export OK en {time.time() - t:.0f}s : {stats}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=80, help="plafond dur du nb de biens enrichis")
    ap.add_argument("--keep", type=int, default=0,
                    help="entonnoir : plafond appliqué au classement par note d'annonce "
                         "(0 = pas de plafond)")
    ap.add_argument("--min-altitude", type=float, default=250.0, dest="min_altitude",
                    help="entonnoir : écarte les communes plus basses (0 = étage sauté)")
    ap.add_argument("--pivot", help="ne collecter qu'autour des pivots dont le nom contient "
                                    "ce texte (ex. « diois »)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--dump", default="",
                    help="écrit les annonces collectées dans ce fichier JSON avant "
                         "l'entonnoir. La collecte dure ~20 min et ne vit qu'en mémoire : "
                         "une interruption la perd entièrement (vécu deux fois).")
    ap.add_argument("--collect-only", action="store_true", dest="collect_only",
                    help="s'arrête après le dump (collecte seule, ~20 min).")
    ap.add_argument("--from-dump", default="", dest="from_dump",
                    help="repart d'un dump au lieu de recollecter (entonnoir + "
                         "enrichissement seuls).")
    ap.add_argument("--pepites", default="",
                    help="resserrage à l'export : « 1:78.5,4:80 ». La base garde tout le "
                         "catalogue de chaque set ; sans ce filtre, l'export republie "
                         "aussi celui des AUTRES sets et annule leur resserrage.")
    ap.add_argument("--no-export", action="store_true",
                    help="ne pas exporter (le seuil des pépites se calibre après coup). "
                         "À n'utiliser que si un export suit dans la foulée : un bien "
                         "collecté mais pas exporté n'existe pas (cf. docs/OPERATIONS.md).")
    ap.add_argument("--rescore-only", action="store_true",
                    help="ne collecte rien : met à jour les sets + re-score + ré-exporte")
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
    ensure_sets(db)

    pepites = _seuils(args.pepites)

    if args.rescore_only:
        _exporter(db, "Re-score seul : ré-export", pepites)
        db.close()
        print("TERMINÉ.", flush=True)
        return 0

    from dataclasses import asdict

    from app.sources.base import NormalizedListing

    existing = {e for (e,) in db.query(Listing.external_id).filter(Listing.source == "bienici").all()}
    collected: dict[str, object] = {}

    if args.from_dump:
        with open(args.from_dump, encoding="utf-8") as fh:
            brut = json.load(fh)
        for d in brut:
            it = NormalizedListing(**d)
            if it.external_id and it.external_id not in existing:
                collected[it.external_id] = it
        print(f"Dump relu : {len(collected)} annonces neuves sur {len(brut)} "
              f"({os.path.abspath(args.from_dump)})", flush=True)
        return _traiter(db, args, collected, pepites)

    src = BienIciSource()
    pivots = PIVOTS
    if args.pivot:
        pivots = [p for p in PIVOTS if args.pivot.lower() in p[0].lower()]
        if not pivots:
            print(f"Aucun pivot ne correspond à « {args.pivot} ». Disponibles : "
                  + ", ".join(p[0] for p in PIVOTS), flush=True)
            return 2
        print(f"Pivots retenus : {', '.join(p[0] for p in pivots)}", flush=True)

    for nom, lat, lon, depts in pivots:
        crit = SearchCriteria(property_types=["maison"], prix_max=PRIX_MAX)
        try:
            items = src.collect_around(crit, lat, lon, depts, radii=(8, 16, 25), cap=None)
        except Exception as e:  # noqa: BLE001
            print(f"  [{nom}] collecte KO: {type(e).__name__}: {str(e)[:60]}", flush=True)
            continue
        neufs = 0
        for it in items:
            if not it.external_id or it.external_id in existing or it.external_id in collected:
                continue
            if it.prix is not None and it.prix > PRIX_MAX:
                continue
            collected[it.external_id] = it
            neufs += 1
        print(f"  [{nom}] {neufs} neufs (sur {len(items)} annonces)", flush=True)

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as fh:
            json.dump([asdict(it) for it in collected.values()], fh, ensure_ascii=False)
        print(f"\nDump écrit : {len(collected)} annonces -> {os.path.abspath(args.dump)}", flush=True)

    if args.collect_only:
        db.close()
        print("Collecte seule (--collect-only) : rien d'enrichi, rien d'exporté.", flush=True)
        return 0

    return _traiter(db, args, collected, pepites)


def _traiter(db, args, collected: dict, pepites: dict) -> int:
    """Entonnoir, enrichissement, mise en base, export. Séparé de la collecte pour être
    rejouable depuis un dump : la collecte dure ~20 min et ne survit pas à une coupure."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from app.models import Listing
    from app.services.entonnoir import appliquer as entonnoir

    print(f"\nEntonnoir sur {len(collected)} annonces :", flush=True)
    todo = entonnoir(list(collected.values()), profil="montagne",
                     min_altitude=args.min_altitude or None, prix_max=PRIX_MAX,
                     garder=args.keep or None)[: args.cap]

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
                print(f"  {i}/{len(todo)} ({time.time() - t0:.0f}s)", flush=True)
    print(f"  enrichis: {len(enriched)} en {time.time() - t0:.0f}s", flush=True)

    for it in enriched:
        row = upsert_listing(db, it)
        row.set_ids = [SET_ID, SOUS_SET_ID]
    db.commit()
    print(f"\nEn base : {db.query(Listing).count()} biens.", flush=True)

    if args.no_export:
        print("\nExport sauté (--no-export) : les biens ne sont QUE dans la base.", flush=True)
    else:
        _exporter(db, "Export", pepites)
    db.close()
    print("TERMINÉ.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
