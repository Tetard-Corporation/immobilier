"""Reconstruit la DB (biens + sets) depuis les fichiers versionnés.

La DB SQLite est un store de travail ÉPHÉMÈRE (conteneur non persistant : elle se
réinitialise au redémarrage) et elle n'est pas versionnable : 108 Mo, au-dessus de la
limite de 100 Mo par fichier de GitHub. Ce sont donc deux fichiers de git qui font foi :

- `backend/data/catalogue.jsonl` — le CATALOGUE, toutes les annonces collectées (7 540),
  écrit par l'export à chaque passage. C'est lui qui reconstruit les biens ;
- `data/data.json` — l'instantané PUBLIÉ. Il porte les sets, et sert de repli pour les
  biens quand la sauvegarde du catalogue n'existe pas encore.

Cette distinction n'est pas cosmétique : `data.json` ne contient que les biens publiés
(612 sur 7 540), donc reconstruire une base à partir de lui seul en amputait 92 % — c'est
le piège que décrit `docs/OPERATIONS.md`, « des biens collectés disparaissent ».

Les IMAGES ne sont PAS stockées en base (trop lourd) : elles restent des fichiers sous
data/photos/, référencés par chemin. L'export réutilise ces fichiers locaux quand une
URL source n'est plus disponible (voir export_static._download_photos).

Usage : python -m app.seed [chemin_vers_data.json]
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from .db import SessionLocal, init_db
from .models import FilterSet, Listing

_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "..", "data", "data.json")
# Sauvegarde texte du CATALOGUE, écrite par l'export (services/export_static.dump_catalogue).
# `data.json` ne contient que les biens PUBLIÉS — 612 sur 7 540 — donc reconstruire la base
# à partir de lui seul en amputait 92 %. Quand ce fichier est là, c'est lui qui fait foi
# pour les biens ; data.json reste la source des SETS.
_CATALOGUE = os.path.join(os.path.dirname(__file__), "..", "data", "catalogue.jsonl")

# Clés de data.json qui ne sont PAS des colonnes Listing à copier telles quelles.
_SKIP = {"id", "source", "external_id", "scores_by_set", "photos",
         "is_favori", "favori_note", "n_photos_source"}
# Idem pour le dump : `photo_urls` est extrait de `raw`, on l'y remet à la restauration
# pour que l'export puisse de nouveau télécharger les images.
_SKIP_CATALOGUE = {"id", "source", "external_id", "photo_urls"}
_DATES = {"first_seen_at", "updated_at"}


def _biens_du_catalogue(path: str) -> list[dict]:
    """Lit le dump JSONL. Une ligne illisible est sautée, pas fatale : une sauvegarde
    tronquée doit restaurer ce qu'elle contient plutôt que de ne rien restaurer."""
    biens = []
    with open(path, encoding="utf-8") as fh:
        for ligne in fh:
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                biens.append(json.loads(ligne))
            except json.JSONDecodeError:
                continue
    return biens


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
                      "zone": s.get("zone") or {},
                      "ancres": s.get("ancres") or {}},
        ))
    db.commit()

    cols = {c.name for c in Listing.__table__.columns}
    n = 0
    # Le catalogue complet quand la sauvegarde est là, les seuls biens publiés sinon.
    catalogue = os.path.exists(_CATALOGUE)
    if catalogue:
        for b in _biens_du_catalogue(_CATALOGUE):
            row = Listing(id=b.get("id"), source=b["source"], external_id=b["external_id"],
                          raw={"photos": b["photo_urls"]} if b.get("photo_urls") else {})
            for k, v in b.items():
                if k in cols and k not in _SKIP_CATALOGUE:
                    # `first_seen_at`/`updated_at` sont des dates, écrites en ISO dans le
                    # dump. SQLAlchemy refuse une chaîne sur une colonne DateTime : sans
                    # cette conversion, la restauration échoue à la première ligne.
                    if k in _DATES and isinstance(v, str):
                        try:
                            v = datetime.fromisoformat(v)
                        except ValueError:
                            continue
                    setattr(row, k, v)
            db.add(row)
            n += 1
    else:
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
    return {"sets": db.query(FilterSet).count(), "biens": n,
            "source": "catalogue.jsonl" if catalogue else "data.json"}


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
