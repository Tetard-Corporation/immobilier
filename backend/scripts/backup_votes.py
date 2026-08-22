#!/usr/bin/env python3
"""Sauvegarde la table `votes` de Supabase dans `data/votes_backup.json` (versionné git).

But : les votes/commentaires du groupe ne doivent JAMAIS être perdus si le projet
Supabase est mis en pause, supprimé, ou recréé. Ce dump committé dans git est la
source de vérité durable ; `restore_votes.py` le réinjecte dans n'importe quel projet.

Aucune dépendance (urllib). Lit la config depuis `config.js` à la racine du dépôt.
Usage :
    python backend/scripts/backup_votes.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))  # backend/scripts/ -> racine
OUT = os.path.join(ROOT, "data", "votes_backup.json")
PAGE = 1000  # PostgREST : lecture paginée pour ne pas plafonner

# Lignes techniques (healthcheck / tests) exclues du backup.
IGNORE_VOTERS = {"__test__"}
IGNORE_BIENS = {"__healthcheck__"}


def _read_config() -> tuple[str, str]:
    txt = open(os.path.join(ROOT, "config.js"), encoding="utf-8").read()
    url = re.search(r'SUPABASE_URL:\s*"([^"]*)"', txt)
    key = re.search(r'SUPABASE_ANON_KEY:\s*"([^"]*)"', txt)
    return (url.group(1) if url else ""), (key.group(1) if key else "")


def _fetch_page(url: str, key: str, offset: int) -> list[dict]:
    q = (f"{url}/rest/v1/votes?select=bien_id,voter,criterion,stars,comment,updated_at"
         f"&order=bien_id.asc,voter.asc,criterion.asc&limit={PAGE}&offset={offset}")
    req = urllib.request.Request(q, headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    url, key = _read_config()
    if not url or not key:
        print("⚠️ SUPABASE_URL / SUPABASE_ANON_KEY absents de config.js.")
        return 1

    rows: list[dict] = []
    offset = 0
    try:
        while True:
            page = _fetch_page(url, key, offset)
            rows.extend(page)
            if len(page) < PAGE:
                break
            offset += PAGE
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Lecture Supabase impossible ({e}).")
        return 1

    kept = [r for r in rows
            if r.get("voter") not in IGNORE_VOTERS and r.get("bien_id") not in IGNORE_BIENS]

    payload = {
        "backed_up_at": datetime.now(timezone.utc).isoformat(),
        "source_url": url,
        "count": len(kept),
        "votes": kept,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=False)

    voters = sorted({r.get("voter") for r in kept})
    print(f"✅ {len(kept)} votes sauvegardés -> {os.path.relpath(OUT, ROOT)}")
    print(f"   votants : {', '.join(voters) or '—'}")
    print("   → commit ce fichier pour rendre la sauvegarde durable (git).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
