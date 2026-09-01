#!/usr/bin/env python3
"""Complète les champs manquants des biens DÉJÀ en base, depuis le texte des annonces.

Le même piège que `reclasser.py` ferme pour l'état du bâti : `nb_chambres`,
`surface_terrain` et les surfaces sont des COLONNES, écrites une fois à la collecte.
L'export les relit telles quelles. Une correction du pipeline de lecture ne s'applique
donc qu'aux biens collectés APRÈS elle, et le catalogue publié continue d'afficher
« ? ch · terrain — ».

Deux sources de complétion, dans cet ordre de confiance :

1. **La charge brute de la source** (`raw`), quand elle contient un champ que le
   connecteur ne lisait pas. Vécu : Leboncoin publie `bedrooms` dans ses attributs et le
   connecteur ne le mappait pas — 2 698 biens sans nombre de chambres alors que la donnée
   était en base, dans la colonne `raw`.
2. **Le texte de l'annonce** (`services/completion`), pour ce que la source n'a jamais dit.

Une valeur donnée par la source n'est jamais écrasée. C'est aussi la limite du script :
il ne remplit que des trous, donc il ne peut pas REVENIR sur ce qu'il a écrit. Après un
changement de règle dans `services/completion.py`, le rejouer ne corrige rien — la base
garde l'ancienne lecture. Repartir de la base d'avant (ou d'une collecte) est le seul
moyen de la faire refléter la règle courante.

Usage :
    python backend/scripts/completer.py --dry-run    # ce qui changerait, sans écrire
    python backend/scripts/completer.py
    python backend/scripts/completer.py --source leboncoin
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
from app.services.completion import CHAMPS, completer  # noqa: E402

# Champs de la charge brute que les connecteurs ne lisaient pas, par source.
# `raw` est la copie fidèle de ce que le portail a répondu : ce qui s'y trouve n'est pas
# une lecture de texte, c'est la donnée du portail.
_DEPUIS_RAW = {
    "leboncoin": {"nb_chambres": ("attributes", "bedrooms"),
                  "surface_terrain": ("attributes", "land_plot_surface")},
    "bienici": {"nb_chambres": (None, "bedroomsQuantity"),
                "surface_terrain": (None, "landSurfaceArea")},
}


def _depuis_raw(row: Listing) -> dict:
    """Champs récupérables dans la charge brute de la source, sans lire de texte."""
    plan = _DEPUIS_RAW.get(row.source)
    raw = row.raw if isinstance(row.raw, dict) else {}
    if not plan or not raw:
        return {}
    attributs = {a.get("key"): a.get("value")
                 for a in (raw.get("attributes") or []) if isinstance(a, dict)}
    trouves = {}
    for champ, (conteneur, cle) in plan.items():
        if getattr(row, champ, None) is not None:
            continue
        brut = attributs.get(cle) if conteneur == "attributes" else raw.get(cle)
        if brut in (None, "", []):
            continue
        try:
            valeur = float(brut)
        except (TypeError, ValueError):
            continue
        if valeur <= 0:
            continue
        trouves[champ] = int(valeur) if champ in ("nb_chambres", "nb_pieces") else valeur
    return trouves


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", dest="dry_run")
    ap.add_argument("--source", help="ne traiter qu'une source (leboncoin, bienici...)")
    ap.add_argument("--exemples", type=int, default=8)
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    q = db.query(Listing)
    if args.source:
        q = q.filter(Listing.source == args.source)
    rows = q.all()

    avant = Counter()
    ecrits_raw = Counter()
    ecrits_texte = Counter()
    exemples = []
    for row in rows:
        for champ in CHAMPS:
            if getattr(row, champ, None) is None:
                avant[champ] += 1
        depuis_raw = _depuis_raw(row)
        for champ, valeur in depuis_raw.items():
            setattr(row, champ, valeur)
            ecrits_raw[champ] += 1
        depuis_texte = completer(row)
        for champ in depuis_texte:
            ecrits_texte[champ] += 1
        if (depuis_raw or depuis_texte) and len(exemples) < args.exemples:
            exemples.append((row.commune, row.prix, {**depuis_raw, **depuis_texte},
                             sorted(depuis_raw)))
        if args.dry_run:
            # `completer`/`_depuis_raw` écrivent sur l'objet ORM : sans rollback, un
            # dry-run finirait par écrire à la première expiration de session.
            db.expunge(row)

    print(f"{len(rows)} biens" + (" (dry-run, rien écrit)" if args.dry_run else ""))
    for champ in CHAMPS:
        manquant = avant[champ]
        gagne = ecrits_raw[champ] + ecrits_texte[champ]
        reste = manquant - gagne
        detail = (f" (source {ecrits_raw[champ]} · texte {ecrits_texte[champ]})"
                  if ecrits_raw[champ] else "")
        pct = f"{100 * gagne // manquant} %" if manquant else "—"
        print(f"  {champ:16} manquant {manquant:5} -> {reste:5}   complété {gagne:5} ({pct}){detail}")
    for commune, prix, champs, via_source in exemples:
        rendu = ", ".join(f"{k}={v}{'*' if k in via_source else ''}" for k, v in champs.items())
        print(f"  · {str(commune)[:24]:24s} {str(int(prix or 0)):>7s}€  {rendu}")
    if exemples:
        print("  (* = lu dans la charge brute de la source, le reste dans le texte)")

    if args.dry_run:
        db.rollback()
    else:
        db.commit()
        print("Écrit. Ré-exporter pour que le site le reflète.")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
