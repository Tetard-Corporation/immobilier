#!/usr/bin/env python3
"""Restaure les votes depuis `data/votes_backup.json` vers le projet Supabase actif.

Réinjecte la sauvegarde git dans la table `votes` (upsert idempotent sur la clé
(bien_id, voter, criterion)) : à utiliser après avoir recréé un projet Supabase, ou
pour migrer les votes vers un nouveau projet. Ne détruit rien (merge-duplicates).

Prérequis : la table `votes` doit exister (voir supabase/migrations/…_votes.sql) et
`config.js` doit pointer vers le bon projet + clé anon.

Aucune dépendance (urllib). Usage :
    python backend/scripts/restore_votes.py            # restaure
    python backend/scripts/restore_votes.py --dry-run  # montre sans écrire
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(ROOT, "data", "votes_backup.json")
BATCH = 500  # upsert par lots pour rester sous les limites de payload


def _read_config() -> tuple[str, str]:
    txt = open(os.path.join(ROOT, "config.js"), encoding="utf-8").read()
    url = re.search(r'SUPABASE_URL:\s*"([^"]*)"', txt)
    key = re.search(r'SUPABASE_ANON_KEY:\s*"([^"]*)"', txt)
    return (url.group(1) if url else ""), (key.group(1) if key else "")


def _upsert(url: str, key: str, batch: list[dict]) -> None:
    # on_conflict + Prefer merge-duplicates = upsert (met à jour la ligne existante).
    endpoint = f"{url}/rest/v1/votes?on_conflict=bien_id,voter,criterion"
    data = json.dumps(batch).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, method="POST", headers={
        "apikey": key, "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()


def main() -> int:
    dry = "--dry-run" in sys.argv
    if not os.path.exists(SRC):
        print(f"⚠️ Sauvegarde absente : {os.path.relpath(SRC, ROOT)} (lance backup_votes.py).")
        return 1
    data = json.load(open(SRC, encoding="utf-8"))
    votes = data.get("votes", [])
    # ne garde que les colonnes de la table (ignore updated_at : régénéré par défaut).
    cols = ("bien_id", "voter", "criterion", "stars", "comment")
    rows = [{k: v.get(k) for k in cols if v.get(k) is not None or k in ("stars", "comment")}
            for v in votes]

    url, key = _read_config()
    if not url or not key:
        print("⚠️ SUPABASE_URL / SUPABASE_ANON_KEY absents de config.js.")
        return 1

    print(f"Sauvegarde : {len(rows)} votes ({data.get('backed_up_at', '?')}) -> {url}")
    if dry:
        for r in rows[:5]:
            print("  ex:", r)
        print(f"[dry-run] {len(rows)} votes seraient upsertés (rien écrit).")
        return 0

    try:
        for i in range(0, len(rows), BATCH):
            _upsert(url, key, rows[i:i + BATCH])
            print(f"  upsert {min(i + BATCH, len(rows))}/{len(rows)}")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Restauration échouée ({e}). La table `votes` existe-t-elle bien ?")
        return 1

    print(f"✅ {len(rows)} votes restaurés dans {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
