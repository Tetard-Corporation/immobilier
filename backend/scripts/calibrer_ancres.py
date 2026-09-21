#!/usr/bin/env python3
"""Recale les deux ancres qui étirent le score sur l'échelle 0-100.

Le match_score est une moyenne pondérée de sous-scores, donc il se concentre
mécaniquement au centre : il faudrait qu'un bien soit excellent PARTOUT pour monter, et
mauvais partout pour descendre. `preferences._contraste` corrige ça par une
transformation affine entre deux ancres — la moyenne pondérée atteignable en bas et en
haut — qui laisse le classement rigoureusement identique et n'en change que l'étalement.

Chaque set déclare les siennes (`criteria["ancres"]`), parce que trois groupes qui ne
cherchent pas la même chose n'atteignent pas les mêmes moyennes. Elles ont été mesurées le
5 septembre 2026 — et **tout changement de barème les périme** : ajouter un critère, en
retirer un du calcul ou en durcir un déplace la moyenne pondérée, donc l'échelle.

Ce script lit un `data.json` EXPORTÉ SANS RESSERRAGE (sinon on ne voit que le haut du
panier, et l'ancre basse est fausse), retrouve la moyenne pondérée de chaque bien en
inversant les ancres de SON set, et propose celles qui étaleraient la distribution sur
toute l'échelle. Il imprime aussi, critère par critère, le pouvoir de discrimination
(poids × écart-type) : un critère que tous les biens réussissent ne classe personne, il
resserre seulement le total.

Les ancres proposées sont des PERCENTILES, pas le minimum et le maximum : un seul bien
aberrant fixerait sinon l'échelle de tous les autres. Elles restent FIXES une fois
écrites dans le code — le score doit dépendre du bien et du set, pas des autres annonces
du lot ; on les recale quand le barème change, pas à chaque export.

Usage :
    python backend/scripts/calibrer_ancres.py ../data/data.json
    python backend/scripts/calibrer_ancres.py ../data/data.json --set 1 --bas 2 --haut 99
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services.preferences import ancres_de  # noqa: E402


def _percentile(valeurs: list[float], p: float) -> float:
    if not valeurs:
        return 0.0
    i = max(0, min(len(valeurs) - 1, round((len(valeurs) - 1) * p / 100)))
    return sorted(valeurs)[i]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_json", nargs="?", default=os.path.join(ROOT, "data", "data.json"))
    ap.add_argument("--set", type=int, default=None, dest="set_id",
                    help="ne calibrer que sur ce set (défaut : tous)")
    ap.add_argument("--bas", type=float, default=2.0, help="percentile de l'ancre basse")
    ap.add_argument("--haut", type=float, default=99.0, help="percentile de l'ancre haute")
    args = ap.parse_args(argv)

    data = json.load(open(args.data_json, encoding="utf-8"))
    # Les ancres appartiennent au SET : on inverse chaque score avec les siennes.
    ancres = {str(s.get("id")): ancres_de({"ancres": s.get("ancres")}) for s in data["sets"]}
    par_set: dict[str, list[float]] = {}
    for b in data["biens"]:
        for sid, sc in (b.get("scores_by_set") or {}).items():
            note = sc.get("match_score")
            if isinstance(note, (int, float)):
                basse, haute = ancres.get(sid, ancres_de(None))
                # Inverse de _contraste : on remonte à la moyenne pondérée brute.
                par_set.setdefault(sid, []).append(basse + note / 100 * (haute - basse))

    toutes: list[tuple[str, float]] = []
    for sid, vals in sorted(par_set.items()):
        if args.set_id is not None and sid != str(args.set_id):
            continue
        basse, haute = ancres.get(sid, ancres_de(None))
        toutes += [(sid, v) for v in vals]
        notes = sorted(vals)
        print(f"set {sid} — {len(vals)} biens · ancres en vigueur {basse} → {haute}")
        print(f"          moyenne pondérée p1 {_percentile(notes, 1):.3f} · "
              f"p25 {_percentile(notes, 25):.3f} · médiane {_percentile(notes, 50):.3f} · "
              f"p75 {_percentile(notes, 75):.3f} · p99 {_percentile(notes, 99):.3f}")
        etendue = [(v - basse) / (haute - basse) * 100 for v in vals]
        moy_e = sum(etendue) / len(etendue)
        print(f"          scores : {min(etendue):.0f} à {max(etendue):.0f} · écart-type "
              f"{(sum((x - moy_e) ** 2 for x in etendue) / len(etendue)) ** 0.5:.1f}")
        bas_s = round(_percentile(notes, args.bas) - 0.02, 2)
        haut_s = round(_percentile(notes, args.haut) + 0.01, 2)
        neuf = [max(0.0, min(100.0, (v - bas_s) / (haut_s - bas_s) * 100)) for v in vals]
        moy_n = sum(neuf) / len(neuf)
        print(f"          → ancres proposées {bas_s} / {haut_s} · écart-type "
              f"{(sum((x - moy_n) ** 2 for x in neuf) / len(neuf)) ** 0.5:.1f} · "
              f"plage {min(neuf):.0f}-{max(neuf):.0f}")

    if not toutes:
        print("Aucun score : le fichier est-il bien un export SANS resserrage ?")
        return 1
    print()

    # Pourquoi le score est plat, critère par critère. Un critère que tous les biens
    # réussissent ne classe personne : il ne fait que remonter tout le monde, et il
    # RÉDUIT l'étalement du total, puisque la moyenne d'un nombre croissant de termes se
    # concentre. Le pouvoir de discrimination, c'est le poids multiplié par l'écart-type.
    exigences = {str(s.get("id")): {p.get("label") or p.get("kind")
                                    for p in (s.get("preferences") or []) if p.get("malus")}
                 for s in data["sets"]}
    for sid, vals in sorted(par_set.items()):
        if args.set_id is not None and sid != str(args.set_id):
            continue
        par_crit: dict[str, tuple[float, list[float]]] = {}
        for b in data["biens"]:
            sc = (b.get("scores_by_set") or {}).get(sid)
            if not sc:
                continue
            for d in sc.get("details") or []:
                if d.get("status") != "ok" or d.get("subscore") is None:
                    continue
                cle = d.get("label") or d.get("kind")
                poids, xs = par_crit.setdefault(cle, (float(d.get("weight") or 0), []))
                xs.append(d["subscore"])
        lignes = []
        for cle, (poids, xs) in par_crit.items():
            if not xs:
                continue
            moy = sum(xs) / len(xs)
            sigma = (sum((x - moy) ** 2 for x in xs) / len(xs)) ** 0.5
            lignes.append((poids * sigma, poids, moy, sigma, len(xs), cle))
        lignes.sort()
        print(f"\nset {sid} — ce qui départage, du plus faible au plus fort "
              f"(poids × écart-type) :")
        for effet, poids, moy, sigma, n, cle in lignes:
            # Une exigence n'est PAS dans la moyenne : son « effet » ci-contre ne veut
            # rien dire, elle n'agit que par sa sanction. On le dit plutôt que de la
            # laisser passer pour un critère qui ne départage rien.
            if cle in exigences.get(sid, set()):
                marque = "  ← EXIGENCE (hors moyenne, ne pénalise qu'en cas d'échec)"
            elif effet < 0.25:
                marque = "  ← ne départage rien"
            else:
                marque = ""
            print(f"  {effet:5.2f}  poids {poids:g}  moyenne {moy:.2f}  σ {sigma:.2f}  "
                  f"({n} biens)  {cle}{marque}")
    print("À reporter dans `criteria[\"ancres\"]` du set concerné "
          "(collect_tetard.ensure_sets / collect_littoral), puis ré-exporter.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
