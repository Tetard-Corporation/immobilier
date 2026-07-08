#!/usr/bin/env python3
"""Rafraîchit les annonces (« biens ») de tous les connecteurs, puis ré-exporte data.json.

Reconstruit le harnais d'ingestion qui vivait hors-dépôt : une table de SECTEURS
(pivot géocodé + rayons) pilote Bien'ici via `collect_around` (collecte exhaustive par
code commune, immunisée contre les homonymes). Les agences sont scrapées depuis
agences.yaml. Leboncoin/SeLoger sont optionnels : ils ne tournent que si un cookie
Datadome frais est en cache (cf. scripts/refresh_datadome.py).

Les jeux de filtres (têtard + sous-sets) sont ré-amorcés depuis data/data.json pour que
l'export recalcule les match_scores. Reproductible : à relancer pour chaque refresh.

Usage
-----
    cd backend
    ./.venv/bin/python scripts/refresh_listings.py                  # tout + export + photos
    ./.venv/bin/python scripts/refresh_listings.py --sources bienici,agences
    ./.venv/bin/python scripts/refresh_listings.py --no-photos      # export sans télécharger les photos
    ./.venv/bin/python scripts/refresh_listings.py --no-export      # ingestion seule (pas de data.json)
    ./.venv/bin/python scripts/refresh_listings.py --enrich         # enrichissement (lent, réseau)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/ sur le path

try:
    from app.config import get_settings
    from app.db import SessionLocal, init_db
    from app.models import FilterSet, Listing, SearchHistory
    from app.schemas import SearchCriteria
    from app.sources import get_registry
    from app.services.search import run_search, upsert_listing
except ModuleNotFoundError as exc:  # mauvais interpréteur (venv backend requis)
    sys.exit(
        f"✗ Dépendance manquante ({exc.name}). Lance avec le venv du backend :\n"
        "    cd backend && ./.venv/bin/python scripts/refresh_listings.py"
    )

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# --- Secteurs cibles (pivot géocodé + rayons croissants en km) ----------------------- #
# lat/lon figés (géocodage BAN, vérifiés par département) pour un refresh déterministe.
# `depts` borne la collecte par code commune ; `radii` = rayons croissants (collecte
# progressive exhaustive). property_types : maison (recherche famille, cf. set têtard).
SECTEURS = [
    {"name": "Bugey / Pays de Gex",        "lat": 46.1280, "lon": 5.8038, "depts": ["01"],            "radii": (8, 16, 25)},
    {"name": "Bauges (École)",             "lat": 45.6462, "lon": 6.1655, "depts": ["73", "74"],      "radii": (8, 16, 25)},
    {"name": "Bauges ouest / Aix-les-Bains","lat": 45.6898, "lon": 5.9097, "depts": ["73"],           "radii": (8, 15)},
    {"name": "Baronnies (Nyons)",          "lat": 44.3556, "lon": 5.1261, "depts": ["26", "84"],      "radii": (8, 16, 22)},
    {"name": "Diois (Die)",                "lat": 44.7500, "lon": 5.3703, "depts": ["26", "05"],      "radii": (8, 16, 25)},
    {"name": "Vercors ouest / Valence",    "lat": 44.9226, "lon": 4.9218, "depts": ["26"],            "radii": (8, 16, 18)},
    {"name": "Cévennes ardéchoises (Les Vans)", "lat": 44.3880, "lon": 4.1162, "depts": ["07", "30", "48"], "radii": (8, 14)},
    {"name": "Ardèche nord (Lamastre)",    "lat": 44.9832, "lon": 4.5848, "depts": ["07"],            "radii": (8, 16, 22)},
    {"name": "Monts d'Ardèche sud (Aubenas)", "lat": 44.6127, "lon": 4.3918, "depts": ["07"],         "radii": (8, 16, 25)},
]
PROPERTY_TYPES = ["maison"]


def seed_filtersets(db) -> int:
    """Recrée les jeux de filtres (têtard + sous-sets) depuis data/data.json.

    Stocke les préférences résolues telles quelles ; la résolution parent→enfant est
    idempotente (l'enfant contient déjà les préférences du parent), donc l'export
    reproduit fidèlement les sets de data.json.
    """
    if db.query(FilterSet).count() > 0:
        return db.query(FilterSet).count()
    path = os.path.join(REPO_ROOT, "data", "data.json")
    if not os.path.exists(path):
        print("  ⚠️  data/data.json absent : aucun jeu de filtres amorcé (scores vides).")
        return 0
    sets = json.load(open(path, encoding="utf-8")).get("sets", [])
    id_map: dict[int, int] = {}            # ancien id data.json -> nouvel id DB
    # Parents d'abord (parent_id absent), puis enfants.
    for s in sorted(sets, key=lambda x: x.get("parent_id") is not None):
        fs = FilterSet(
            name=s.get("name") or "set",
            criteria={"preferences": s.get("preferences") or []},
            parent_id=id_map.get(s.get("parent_id")) if s.get("parent_id") else None,
        )
        db.add(fs)
        db.flush()
        id_map[s.get("id")] = fs.id
    db.commit()
    return len(sets)


def run_bienici(db, *, enrich: bool) -> int:
    """Collecte exhaustive Bien'ici par secteur (collect_around), upsert + historique."""
    bi = get_registry()["bienici"]
    crit = SearchCriteria(property_types=PROPERTY_TYPES)
    total = 0
    for sec in SECTEURS:
        try:
            items = bi.collect_around(crit, sec["lat"], sec["lon"], sec["depts"], radii=tuple(sec["radii"]))
        except Exception as exc:  # un secteur qui échoue ne fait pas tomber le reste
            print(f"    ✗ {sec['name']}: {exc}")
            continue
        for it in items:
            upsert_listing(db, it)
        db.add(SearchHistory(
            source="bienici",
            criteria={"secteur": sec["name"], "lat": sec["lat"], "lon": sec["lon"],
                      "depts": sec["depts"], "radii": list(sec["radii"]), "property_types": PROPERTY_TYPES},
            nb_results=len(items), enriched=False, top_results=[],
        ))
        db.commit()
        print(f"    ✓ {sec['name']:34} {len(items):3} biens")
        total += len(items)
    return total


def run_agences(db) -> int:
    from app.services.agences_ingest import ingest
    res = ingest(db)
    print(f"    ✓ agences : {res.get('ingested', 0)} biens (extracteur={res.get('extractor')})")
    return res.get("ingested", 0)


def run_datadome_source(db, name: str) -> int:
    """Leboncoin/SeLoger : 1 recherche par pivot de secteur (code postal). Best-effort,
    uniquement si la source est disponible (cookie Datadome frais en cache)."""
    src = get_registry()[name]
    if not src.available:
        print(f"    – {name} ignoré (pas de cookie Datadome ; lance scripts/refresh_datadome.py {name}).")
        return 0
    from app.services.geo import geocode_locality  # noqa: F401  (déjà utilisé en amont)
    total = 0
    for sec in SECTEURS:
        # Code postal du pivot via géocodage (déterministe, déjà vérifié).
        try:
            g = geocode_locality(sec["name"].split("(")[-1].strip(") ") or sec["name"])
            cp = (g or {}).get("code_postal")
            if not cp:
                continue
            crit = SearchCriteria(code_postal=cp, property_types=PROPERTY_TYPES)
            res = run_search(db, name, crit, enrich=False, record_history=True)
            total += len(res.results)
        except Exception as exc:
            print(f"    ✗ {name} {sec['name']}: {exc}")
    print(f"    ✓ {name} : {total} biens (pivots de secteur)")
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="Rafraîchit les annonces de tous les connecteurs.")
    ap.add_argument("--sources", default="bienici,agences,leboncoin,seloger",
                    help="Connecteurs à interroger (séparés par des virgules).")
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "data"), help="Dossier d'export (data.json).")
    ap.add_argument("--no-photos", action="store_true", help="Ne pas (re)télécharger les photos.")
    ap.add_argument("--no-export", action="store_true", help="Ingestion seule, sans réécrire data.json.")
    ap.add_argument("--enrich", action="store_true", help="Enrichissement (zonage, risques… ; lent).")
    args = ap.parse_args()
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    init_db()
    db = SessionLocal()
    print("=" * 64)
    print("RAFRAÎCHISSEMENT DES ANNONCES  ·  sources:", ", ".join(sources))
    print("=" * 64)

    n_sets = seed_filtersets(db)
    print(f"[sets] {n_sets} jeu(x) de filtres en base")

    if "bienici" in sources:
        print("\n[Bien'ici] collecte exhaustive par secteur…")
        run_bienici(db, enrich=args.enrich)
    if "agences" in sources:
        print("\n[Agences] scraping des sites (agences.yaml)…")
        run_agences(db)
    for name in ("leboncoin", "seloger"):
        if name in sources:
            print(f"\n[{name}] recherche par pivot de secteur…")
            run_datadome_source(db, name)

    # Récap base
    from sqlalchemy import func, select
    by_src = db.execute(select(Listing.source, func.count()).group_by(Listing.source)).all()
    total = sum(c for _, c in by_src)
    print("\n" + "-" * 64)
    print(f"BASE : {total} biens — " + ", ".join(f"{s}={c}" for s, c in by_src))

    if not args.no_export:
        print("\n[Export] génération de data.json" + (" (sans photos)" if args.no_photos else " + photos…"))
        from app.services.export_static import export_to_dir
        stats = export_to_dir(db, args.out, download_photos=not args.no_photos)
        print(f"  ✓ {args.out}/data.json : {stats}")

    db.close()
    print("=" * 64, "\nTERMINÉ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
