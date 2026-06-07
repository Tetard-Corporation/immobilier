#!/usr/bin/env python3
"""Applique proposal.json au(x) set(s) en base. À lancer depuis backend/ (venv actif).

  python ../.claude/skills/converge-filters/apply_proposal.py --global --subsets

- --global  : met à jour les poids du set principal (versionne l'ancien dans criteria._history)
- --subsets : crée/met à jour un sous-set par utilisateur (poids boostés/réduits selon ses votes)

Ne touche PAS aux biens : c'est l'export (recalcul) qui recompute tout ensuite.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from sqlalchemy.orm.attributes import flag_modified

from app.db import SessionLocal
from app.models import FilterSet

HERE = os.path.dirname(os.path.abspath(__file__))
PROPOSAL = json.load(open(os.path.join(HERE, "proposal.json"), encoding="utf-8"))


def apply_global(db) -> None:
    main = db.query(FilterSet).filter_by(id=PROPOSAL["set_id"]).one()
    crit = dict(main.criteria or {})
    prefs = crit.get("preferences", [])
    # versionne l'ancien jeu de poids
    crit.setdefault("_history", []).append({
        "at": datetime.now(timezone.utc).isoformat(),
        "weights": {(p.get("label") or p.get("kind")): p.get("weight") for p in prefs},
    })
    new_w = {g["label"]: g["weight_proposed"] for g in PROPOSAL["global_weights"]}
    changed = []
    for p in prefs:
        lbl = p.get("label") or p.get("kind")
        if lbl in new_w and new_w[lbl] != p.get("weight"):
            changed.append(f"{lbl}: {p.get('weight')}→{new_w[lbl]}")
            p["weight"] = new_w[lbl]
    crit["preferences"] = prefs
    main.criteria = crit
    flag_modified(main, "criteria")
    db.commit()
    print(f"Set « {main.name} » mis à jour. Changements: {changed or 'aucun'}")


def apply_subsets(db) -> None:
    main = db.query(FilterSet).filter_by(id=PROPOSAL["set_id"]).one()
    parent_prefs = (main.criteria or {}).get("preferences", [])
    by_label = {(p.get("label") or p.get("kind")): p for p in parent_prefs}
    for user, prof in PROPOSAL.get("per_user", {}).items():
        overrides = []
        for label, mean in prof.get("top", []):
            base = by_label.get(label)
            if not base:
                continue
            w = base.get("weight", 1)
            if mean >= 4:      # valorisé -> on renforce
                nw = min(5, w + 1)
            elif mean <= 2:    # peu valorisé -> on réduit
                nw = max(1, w - 1)
            else:
                continue
            overrides.append({"kind": base.get("kind"), "params": base.get("params"),
                              "weight": nw, "label": label})
        if not overrides:
            continue
        sub = db.query(FilterSet).filter_by(name=user, parent_id=main.id).one_or_none()
        crit = {"preferences": overrides}
        if sub:
            sub.criteria = crit; flag_modified(sub, "criteria")
            print(f"Sous-set « {user} » mis à jour ({len(overrides)} overrides).")
        else:
            db.add(FilterSet(name=user, parent_id=main.id,
                             description=f"Sous-set perso de {user} (dérivé des votes).",
                             criteria=crit))
            print(f"Sous-set « {user} » créé ({len(overrides)} overrides).")
        db.commit()


if __name__ == "__main__":
    db = SessionLocal()
    if "--global" in sys.argv:
        apply_global(db)
    if "--subsets" in sys.argv:
        apply_subsets(db)
    if "--global" not in sys.argv and "--subsets" not in sys.argv:
        print("Rien à faire. Passe --global et/ou --subsets.")
