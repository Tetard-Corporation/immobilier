#!/usr/bin/env python3
"""Ménage du catalogue : supprime les biens qu'aucun set ne note au-dessus d'un seuil.

Pourquoi. Le catalogue grossit à chaque collecte et ne rétrécit jamais : 7 540 annonces
pour un panier d'une quinzaine de pépites. Ce qui ne remonte dans AUCUN set n'a aucune
chance d'être regardé un jour — il coûte du temps de mesure (chaque bien géolocalisé est
un point à réchauffer), de l'espace dans la sauvegarde versionnée, et du bruit à chaque
export.

Comment le score est obtenu. Le script ne recalcule rien à sa façon : il fait tourner
l'EXPORT sans resserrage dans un dossier temporaire, et lit les `scores_by_set` qu'il
produit. C'est donc exactement la note qui fera foi sur le site, paliers compris.

Un bien est supprimé quand sa MEILLEURE note, tous sets confondus, passe sous le seuil.
Sont épargnés quoi qu'il arrive :

- les FAVORIS (quelqu'un les a mis de côté) et les biens déjà NOTÉS par quelqu'un — une
  note basse du moteur ne périme pas le vote d'une personne ;
- les biens sans note du tout (aucun set ne les couvre) : ils ne sont pas mauvais, ils
  ne sont pas jugés, et c'est une autre décision que celle-ci.

Usage :
    python backend/scripts/menage.py --dry-run     # ce qui partirait, sans rien toucher
    python backend/scripts/menage.py --seuil 50
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import re  # noqa: E402
import urllib.request  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Listing, SavedListing  # noqa: E402
from app.services.export_static import build_dataset, dump_catalogue  # noqa: E402


def _biens_votes() -> set[str]:
    """Les CLÉS DE VOTE sur lesquelles quelqu'un a posé une note ou un commentaire.

    Une clé de vote est `source__external_id` (`app.js: voteKey`), pas l'`external_id`
    seul. Comparer les deux ne lève aucune correspondance et la protection ne protège
    RIEN, sans que rien ne le dise — deux biens notés 1★ par le groupe sont partis comme
    ça. D'où le garde-fou ajouté dans `main` : une liste de votes qui ne recoupe aucun
    bien de la base est une anomalie, pas un catalogue sans vote.

    Supprimer un bien voté, c'est perdre le vote : il vit dans Supabase, indexé par
    l'identifiant du bien, et rien ne le rattacherait à quoi que ce soit ensuite. Une
    note basse du moteur ne périme pas le jugement d'une personne — c'est même le cas où
    les deux méritent d'être comparés. En cas de panne réseau on renvoie None, et
    l'appelant s'arrête plutôt que de supprimer à l'aveugle.
    """
    txt = open(os.path.join(ROOT, "config.js"), encoding="utf-8").read()
    url = re.search(r'SUPABASE_URL:\s*"([^"]*)"', txt)
    key = re.search(r'SUPABASE_ANON_KEY:\s*"([^"]*)"', txt)
    if not url or not key or not url.group(1):
        return set()
    req = urllib.request.Request(
        f"{url.group(1)}/rest/v1/votes?select=bien_id",
        headers={"apikey": key.group(1), "Authorization": f"Bearer {key.group(1)}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return {v["bien_id"] for v in json.loads(r.read()) if v.get("bien_id")}


def _meilleures_notes(db) -> dict[int, float | None]:
    """{id du bien: meilleure note tous sets confondus}. None = noté par aucun set."""
    with tempfile.TemporaryDirectory() as tmp:
        data = build_dataset(db, out_dir=tmp, download_photos=False)
    notes = {}
    for b in data["biens"]:
        scores = [s.get("match_score") for s in (b.get("scores_by_set") or {}).values()
                  if isinstance(s.get("match_score"), (int, float))]
        notes[b["id"]] = max(scores) if scores else None
    return notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seuil", type=float, default=50.0)
    ap.add_argument("--dry-run", action="store_true", dest="dry")
    args = ap.parse_args(argv)

    db = SessionLocal()
    try:
        total = db.query(Listing).count()
        print(f"{total} biens en base. Calcul des notes (export à blanc)…", flush=True)
        notes = _meilleures_notes(db)

        favoris = {sv.listing_id for sv in db.query(SavedListing).all()}
        try:
            votes = _biens_votes()
        except Exception as e:
            print(f"ABANDON : impossible de lire les votes Supabase ({e}). "
                  "Supprimer sans cette liste effacerait des biens que le groupe a notés.")
            return 1
        # La clé de vote est `source__external_id`, reconstruite ici plutôt que cherchée
        # en base : un `IN` sur l'`external_id` seul ne correspond à rien.
        votes_ids = {r.id for r in db.query(Listing).all()
                     if f"{r.source}__{r.external_id}" in votes} if votes else set()
        proteges = favoris | votes_ids
        print(f"  protégés : {len(favoris)} favoris · {len(votes_ids)} biens notés ou commentés")
        # Des votes existent mais aucun ne tombe sur un bien de la base : c'est le signe
        # d'un format de clé qui a changé, pas d'un catalogue sans vote. On s'arrête
        # plutôt que de supprimer en croyant protéger.
        if votes and not votes_ids:
            print(f"ABANDON : {len(votes)} votes lus, aucun ne correspond à un bien en base. "
                  "Le format de la clé de vote a dû changer (attendu « source__id »).")
            return 1

        a_supprimer, sans_note, epargnes = [], 0, 0
        for bien_id, note in notes.items():
            if note is None:
                sans_note += 1
                continue
            if note >= args.seuil:
                continue
            if bien_id in proteges:
                epargnes += 1
                continue
            a_supprimer.append(bien_id)

        notees = total - sans_note
        print(f"  {notees} biens notés · {sans_note} sans note (aucun set ne les couvre)")
        print(f"  {len(a_supprimer)} sous {args.seuil:g} à supprimer "
              f"({len(a_supprimer) / max(notees, 1) * 100:.0f} % des biens notés)"
              f"{f' · {epargnes} épargnés (favoris ou notés)' if epargnes else ''}")
        if args.dry:
            exemples = [b for b in db.query(Listing).filter(Listing.id.in_(a_supprimer[:8])).all()]
            for b in exemples:
                print(f"    ex. {b.commune} — {b.prix} € — {b.source} — note {notes[b.id]:.1f}")
            print("\n(--dry-run : rien n'a été supprimé)")
            return 0
        if not a_supprimer:
            return 0

        # Par lots : SQLite plafonne le nombre de paramètres d'une requête.
        for i in range(0, len(a_supprimer), 500):
            lot = a_supprimer[i:i + 500]
            db.query(Listing).filter(Listing.id.in_(lot)).delete(synchronize_session=False)
        db.commit()
        reste = db.query(Listing).count()
        print(f"  supprimés. {reste} biens restants.")

        # La sauvegarde versionnée doit suivre, sinon le prochain `app.seed` les remet.
        info = dump_catalogue(db, os.path.join(ROOT, "backend", "data", "catalogue.jsonl"))
        print(f"  catalogue.jsonl réécrit : {info['biens']} biens, "
              f"{info['octets'] / 1e6:.1f} Mo.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
