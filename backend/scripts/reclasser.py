#!/usr/bin/env python3
"""Re-classe l'état des biens DÉJÀ en base avec la version courante de `classify`.

Le piège que ce script existe pour fermer : `condition` et `niveau_travaux` sont des
COLONNES, écrites une fois à la collecte. L'export les relit telles quelles — il ne
reclasse rien. Une correction de `services/classify.py` ne s'applique donc qu'aux biens
collectés APRÈS elle, et le catalogue continue de porter l'ancien verdict.

Vécu le 31 août : la grange de Jarrier, dont l'annonce dit « à rénover entièrement »,
est restée à « à rénover » — donc admissible au palier travaux — après la correction qui
la rangeait en gros travaux. Le code était juste, le site montrait toujours la ruine.

Usage :
    python backend/scripts/reclasser.py --dry-run   # ce qui changerait, sans écrire
    python backend/scripts/reclasser.py
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Listing  # noqa: E402
from app.services.classify import classify  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", dest="dry_run")
    ap.add_argument("--exemples", type=int, default=10,
                    help="nb de reclassements détaillés à afficher")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    rows = db.query(Listing).all()
    change = Counter()
    exemples = []
    for r in rows:
        # Mêmes entrées que `services/enrich.annotate` — description ET adresse/titre.
        # Ne passer que la description reclasserait sur moins de texte que la
        # collecte, donc produirait des « corrections » qui n'en sont pas.
        res = classify(r.description, r.adresse)
        if res["condition"] == r.condition:
            continue
        change[(r.condition, res["condition"])] += 1
        if len(exemples) < args.exemples:
            exemples.append((r.commune, r.prix, r.condition, res["condition"],
                             ((r.adresse or "") + " " + (r.description or ""))[:90].replace("\n", " ")))
        if not args.dry_run:
            r.condition = res["condition"]
            r.niveau_travaux = res["niveau_travaux"]
    total = sum(change.values())
    print(f"{len(rows)} biens · {total} reclassements"
          + (" (dry-run, rien écrit)" if args.dry_run else ""))
    for (avant, apres), n in change.most_common():
        print(f"  {str(avant):14s} -> {str(apres):14s} : {n}")
    for c, prix, a, b, txt in exemples:
        print(f"  · {str(c)[:22]:22s} {str(int(prix or 0)):>7s}€  {a} -> {b}  {txt}")
    if not args.dry_run:
        db.commit()
        print("Écrit. Ré-exporter pour que le site le reflète.")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
