"""Reconstruit la DB (biens + sets) depuis data/data.json.

La DB SQLite est un store de travail ÉPHÉMÈRE (conteneur non persistant : elle se
réinitialise au redémarrage). `data/data.json`, lui, est versionné dans git : c'est la
source de vérité durable. Ce seed permet de reconstruire une DB fonctionnelle après un
reset, sans re-scraper.

Les IMAGES ne sont PAS stockées en base (trop lourd) : elles restent des fichiers sous
data/photos/, référencés par chemin. L'export réutilise ces fichiers locaux quand une
URL source n'est plus disponible (voir export_static._download_photos).

Usage : python -m app.seed [chemin_vers_data.json]
"""

from __future__ import annotations

import json
import os
import sys

from .db import SessionLocal, init_db
from .models import FilterSet, Listing

_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "..", "data", "data.json")

# Clés de data.json qui ne sont PAS des colonnes Listing à copier telles quelles.
_SKIP = {"id", "source", "external_id", "scores_by_set", "photos",
         "is_favori", "favori_note", "n_photos_source"}


def seed_from_data_json(path: str = _DEFAULT) -> dict:
    """Vide puis reconstruit filter_sets + listings depuis data.json. Renvoie un récap.

    Ne touche pas aux votes (stockés dans Supabase, indépendants de cette DB)."""
    init_db()
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    db = SessionLocal()
    db.query(Listing).delete()
    db.query(FilterSet).delete()
    db.commit()

    for s in data.get("sets", []):
        db.add(FilterSet(
            id=s["id"], name=s["name"], parent_id=s.get("parent_id"),
            description=s.get("description"),
            criteria={"property_types": s.get("property_types", ["maison"]),
                      "preferences": s.get("preferences", []),
                      "exigences": s.get("exigences", [])},
        ))
    db.commit()

    cols = {c.name for c in Listing.__table__.columns}
    n = 0
    for b in data.get("biens", []):
        # Appartenance aux sets = sets pour lesquels le bien a un score.
        set_ids = sorted(int(k) for k in (b.get("scores_by_set") or {}))
        row = Listing(id=b.get("id"), source=b["source"], external_id=b["external_id"],
                      set_ids=set_ids, raw={})
        for k, v in b.items():
            if k in cols and k not in _SKIP:
                setattr(row, k, v)
        db.add(row)
        n += 1
    db.commit()
    return {"sets": db.query(FilterSet).count(), "biens": n}


def seed_if_empty(path: str = _DEFAULT) -> dict | None:
    """Seed la DB depuis data.json UNIQUEMENT si elle est vide (aucun bien) et que
    data.json existe. Renvoie le récap si un seed a eu lieu, sinon None. Sûr au
    démarrage (idempotent : ne fait rien si des biens sont déjà présents)."""
    from .models import Listing

    db = SessionLocal()
    try:
        has_biens = db.query(Listing).first() is not None
    except Exception:
        has_biens = False  # table absente/illisible -> traiter comme vide
    finally:
        db.close()
    if has_biens or not os.path.exists(path):
        return None
    return seed_from_data_json(path)


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT
    print(seed_from_data_json(arg))
