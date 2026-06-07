#!/usr/bin/env python3
"""Analyse les votes + commentaires (Supabase) pour proposer une convergence du set.

Sortie :
- un rapport Markdown (stdout) lisible par l'agent ;
- un fichier `proposal.json` (à côté du script) : nouveaux poids proposés pour le set
  global (si convergence), profils par utilisateur (sous-sets), et signaux de
  commentaires pouvant indiquer des erreurs backend.

Lit la config Supabase et les scores algo depuis le dépôt (aucune dépendance backend).
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))  # .claude/skills/<x>/ -> repo root
MIN_PAIRS = 4          # paires (vote critère, note globale) mini pour estimer une importance
MIN_USER_VOTES = 8     # votes mini pour proposer/mettre à jour un sous-set utilisateur
OVERALL = "__overall__"
FAVORI = "__favori__"


def _read_config() -> tuple[str, str]:
    txt = open(os.path.join(ROOT, "config.js"), encoding="utf-8").read()
    url = re.search(r'SUPABASE_URL:\s*"([^"]*)"', txt)
    key = re.search(r'SUPABASE_ANON_KEY:\s*"([^"]*)"', txt)
    return (url.group(1) if url else ""), (key.group(1) if key else "")


def _fetch_votes(url: str, key: str) -> list[dict]:
    if not url or not key:
        return []
    req = urllib.request.Request(
        f"{url}/rest/v1/votes?select=bien_id,voter,criterion,stars,comment",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def main() -> None:
    data = json.load(open(os.path.join(ROOT, "data", "data.json"), encoding="utf-8"))
    sets = data.get("sets", [])
    biens = data.get("biens", [])
    bien_by_key = {f"{b['source']}__{b['external_id']}": b for b in biens}

    # Set principal (sans parent) = set du groupe à faire converger.
    main_set = next((s for s in sets if not s.get("parent_id")), sets[0] if sets else None)
    if not main_set:
        print("Aucun set trouvé."); return
    prefs = main_set.get("preferences", [])

    url, key = _read_config()
    try:
        rows = _fetch_votes(url, key)
    except Exception as e:
        print(f"⚠️ Lecture Supabase impossible ({e}). Renseigne config.js."); return

    # Index : votes[bien][criterion][voter] = {stars, comment}
    votes: dict = {}
    voters: set = set()
    n_stars = n_comments = 0
    ignore_voters = {"__test__"}  # lignes de healthcheck / test
    for r in rows:
        if r.get("voter") in ignore_voters or r.get("bien_id") == "__healthcheck__":
            continue
        crit = r.get("criterion") or OVERALL
        votes.setdefault(r["bien_id"], {}).setdefault(crit, {})[r["voter"]] = r
        voters.add(r["voter"])
        if isinstance(r.get("stars"), int):
            n_stars += 1
        if r.get("comment"):
            n_comments += 1

    out = []
    out.append(f"# Convergence du set « {main_set['name']} »\n")
    out.append(f"- Votants actifs : **{len(voters)}** ({', '.join(sorted(voters)) or '—'})")
    out.append(f"- Notes (étoiles) : **{n_stars}** · Commentaires : **{n_comments}**")
    if n_stars < MIN_PAIRS * 2:
        out.append("\n⚠️ **Trop peu de votes** pour une convergence fiable. "
                   "Relance ce skill quand le groupe aura davantage voté.")

    # ---- Importance par critère (corrélation note-critère <-> note globale) ----
    proposal_global = []
    out.append("\n## Poids proposés (set global)\n")
    out.append("| Critère | Poids actuel | Algo moy | Groupe moy | Corrélation→global | Poids proposé | Confiance |")
    out.append("|---|---|---|---|---|---|---|")
    for p in prefs:
        label = p.get("label") or p.get("kind")
        w_cur = p.get("weight", 1)
        # paires (note du critère, note globale) pour le même (utilisateur, bien)
        cx, cy, crit_vals, algo_vals = [], [], [], []
        for bkey, bcrits in votes.items():
            cvotes = bcrits.get(label, {})
            ovotes = bcrits.get(OVERALL, {})
            for u, cv in cvotes.items():
                if isinstance(cv.get("stars"), int):
                    crit_vals.append(cv["stars"])
                    if isinstance(ovotes.get(u, {}).get("stars"), int):
                        cx.append(cv["stars"]); cy.append(ovotes[u]["stars"])
            # score algo du critère pour ce bien (depuis details du set)
            b = bien_by_key.get(bkey)
            if b:
                sb = (b.get("scores_by_set") or {}).get(str(main_set["id"]), {})
                d = next((x for x in sb.get("details", []) if (x.get("label") or x.get("kind")) == label), None)
                if d and isinstance(d.get("subscore"), (int, float)):
                    algo_vals.append(d["subscore"])
        r = _pearson(cx, cy)
        n = len(cx)
        group_mean = (sum(crit_vals) / len(crit_vals) / 5) if crit_vals else None
        algo_mean = (sum(algo_vals) / len(algo_vals)) if algo_vals else None
        if r is not None and n >= MIN_PAIRS:
            importance = max(0.0, min(1.0, r))
            w_new = max(1, min(5, round(1 + 4 * importance)))
            conf = "haute" if n >= 10 else "moyenne"
        else:
            w_new = w_cur
            conf = "faible (données insuffisantes)"
        proposal_global.append({"kind": p.get("kind"), "label": label, "params": p.get("params"),
                                "weight_current": w_cur, "weight_proposed": w_new,
                                "n_pairs": n, "corr": r, "group_mean": group_mean, "algo_mean": algo_mean})
        fmt = lambda v: "—" if v is None else f"{v:.2f}"
        out.append(f"| {label} | {w_cur} | {fmt(algo_mean)} | {fmt(group_mean)} | {fmt(r)} ({n}) | "
                   f"**{w_new}**{' ⬆️' if w_new>w_cur else ' ⬇️' if w_new<w_cur else ''} | {conf} |")

    # ---- Convergence : désaccord inter-utilisateurs ----
    out.append("\n## Convergence / divergences\n")
    diverging = []
    for p in prefs:
        label = p.get("label") or p.get("kind")
        per_user = {}
        for bcrits in votes.values():
            for u, cv in bcrits.get(label, {}).items():
                if isinstance(cv.get("stars"), int):
                    per_user.setdefault(u, []).append(cv["stars"])
        means = [sum(v) / len(v) for v in per_user.values() if v]
        if len(means) >= 2:
            spread = max(means) - min(means)
            if spread >= 2.0:  # écart d'au moins 2 étoiles entre utilisateurs
                diverging.append((label, round(spread, 1), {u: round(sum(v) / len(v), 1) for u, v in per_user.items()}))
    if diverging:
        out.append("Critères où le groupe **diverge** (à garder en sous-set perso plutôt qu'imposer) :")
        for label, spread, by in diverging:
            out.append(f"- **{label}** (écart {spread}★) : {by}")
    else:
        out.append("Pas de divergence forte détectée → un set commun est envisageable.")

    # ---- Profils par utilisateur (sous-sets) ----
    out.append("\n## Profils par utilisateur (sous-sets)\n")
    per_user_profiles = {}
    for u in sorted(voters):
        crit_means = {}
        nv = 0
        for bcrits in votes.values():
            for crit, uv in bcrits.items():
                if crit in (OVERALL, FAVORI) or crit.startswith("invest:") or crit.startswith("risk:"):
                    continue
                cv = uv.get(u)
                if cv and isinstance(cv.get("stars"), int):
                    crit_means.setdefault(crit, []).append(cv["stars"]); nv += 1
        if nv >= MIN_USER_VOTES:
            tops = sorted(((c, sum(v) / len(v)) for c, v in crit_means.items()), key=lambda x: -x[1])
            per_user_profiles[u] = {"n_votes": nv, "top": tops[:5]}
            out.append(f"- **{u}** ({nv} votes) — valorise surtout : "
                       + ", ".join(f"{c} ({m:.1f}★)" for c, m in tops[:4]))
        else:
            out.append(f"- {u} : {nv} votes (insuffisant pour un sous-set, min {MIN_USER_VOTES})")

    # ---- Commentaires : digest + signaux d'erreur backend ----
    out.append("\n## Commentaires\n")
    err_re = re.compile(r"\b(pas|n['e ]|faux|erreur|incorrect|bug|en fait|loin|à c[ôo]t[ée]|"
                        r"trop|km|min|min\.|metres?|m[èe]tres?|aucun|jamais|au contraire)\b", re.I)
    comment_list, flagged = [], []
    for bkey, bcrits in votes.items():
        b = bien_by_key.get(bkey, {})
        commune = b.get("commune", bkey)
        for crit, uv in bcrits.items():
            for u, cv in uv.items():
                c = cv.get("comment")
                if not c:
                    continue
                crit_label = "global" if crit == OVERALL else crit
                entry = {"voter": u, "commune": commune, "bien_id": bkey, "criterion": crit_label,
                         "stars": cv.get("stars"), "comment": c}
                comment_list.append(entry)
                if err_re.search(c):
                    flagged.append(entry)
    out.append(f"{len(comment_list)} commentaire(s). "
               f"**{len(flagged)}** pouvant signaler une erreur de donnée/scoring (à vérifier) :")
    for e in flagged[:25]:
        out.append(f"- [{e['commune']} · {e['criterion']}] {e['voter']} ({e['stars']}★) : « {e['comment']} »")

    # ---- proposal.json ----
    proposal = {
        "set_id": main_set["id"], "set_name": main_set["name"],
        "data": {"voters": sorted(voters), "n_stars": n_stars, "n_comments": n_comments},
        "global_weights": proposal_global,
        "diverging": [{"label": l, "spread": s, "by_user": by} for l, s, by in diverging],
        "per_user": {u: {"n_votes": p["n_votes"], "top": [[c, m] for c, m in p["top"]]}
                     for u, p in per_user_profiles.items()},
        "comments": comment_list,
        "comments_flagged": flagged,
    }
    json.dump(proposal, open(os.path.join(HERE, "proposal.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    out.append(f"\n→ Proposition machine écrite dans `{os.path.relpath(os.path.join(HERE, 'proposal.json'), ROOT)}`")
    print("\n".join(out))


if __name__ == "__main__":
    main()
