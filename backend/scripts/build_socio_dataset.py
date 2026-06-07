#!/usr/bin/env python3
"""Construit `data/communes_socio.csv` (code_insee, age_median, part_gauche).

- `part_gauche` : part des voix de gauche au 1er tour de la présidentielle 2022,
  par commune (open data Ministère de l'Intérieur via data.gouv — source vérifiée).
- `age_median` : optionnel, fusionné depuis un CSV INSEE fourni (`--age-csv`),
  attendu avec des colonnes code commune + âge médian (voir options).

Exemples
--------
    # Télécharge les résultats électoraux et génère le dataset (part_gauche seul) :
    python scripts/build_socio_dataset.py

    # Avec un fichier d'âge médian INSEE :
    python scripts/build_socio_dataset.py --age-csv age_median_communes.csv \
        --age-code-col CODGEO --age-value-col AGEMED

    # À partir d'un fichier électoral déjà téléchargé :
    python scripts/build_socio_dataset.py --election-file 04-resultats-par-commune.csv

Sources :
  Élection 2022 T1 par commune :
  https://www.data.gouv.fr/fr/datasets/6253d5639cd8ac26faea5efe/
  Âge médian / structure par âge : INSEE (recensement, base "struct-pop").
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
import unicodedata

import httpx

ELECTION_URL = (
    "https://static.data.gouv.fr/resources/"
    "resultats-du-premier-tour-de-lelection-presidentielle-2022-par-commune-et-par-departement/"
    "20220413-153144/04-resultats-par-commune.csv"
)
# Candidats de gauche au 1er tour 2022 (comparaison sans accents, en majuscules).
GAUCHE = {"MELENCHON", "ROUSSEL", "JADOT", "HIDALGO", "POUTOU", "ARTHAUD"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).upper().strip()


def _insee(dep_code: str, commune_code: str) -> str:
    dep_code, commune_code = dep_code.strip(), commune_code.strip()
    if len(dep_code) >= 3:  # DOM (971..976)
        return dep_code + commune_code.zfill(2)
    return dep_code.zfill(2) + commune_code.zfill(3)


def _download(url: str, dest: str) -> None:
    print(f"Téléchargement {url} ...", file=sys.stderr)
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)


def part_gauche_by_commune(path: str) -> dict[str, float]:
    agg: dict[str, dict] = {}
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                insee = _insee(r["dep_code"], r["commune_code"])
                exprimes = float(r["exprimes_nb"] or 0)
            except (KeyError, ValueError):
                continue
            e = agg.setdefault(insee, {"expr": exprimes, "g": 0.0})
            if _norm(r.get("cand_nom", "")) in GAUCHE:
                e["g"] += float(r.get("cand_nb_voix") or 0)
    return {k: round(v["g"] / v["expr"], 3) for k, v in agg.items() if v["expr"] > 0}


def age_by_commune(path: str, code_col: str, value_col: str) -> dict[str, float]:
    ages: dict[str, float] = {}
    with open(path, encoding="utf-8") as fh:
        # détecte ; ou , automatiquement
        sample = fh.read(4096)
        fh.seek(0)
        delim = ";" if sample.count(";") > sample.count(",") else ","
        for r in csv.DictReader(fh, delimiter=delim):
            code = (r.get(code_col) or "").strip()
            try:
                ages[code] = round(float((r.get(value_col) or "").replace(",", ".")), 1)
            except ValueError:
                continue
    return ages


def main() -> None:
    ap = argparse.ArgumentParser(description="Génère data/communes_socio.csv")
    ap.add_argument("--election-url", default=ELECTION_URL)
    ap.add_argument("--election-file", help="CSV électoral local (sinon téléchargé)")
    ap.add_argument("--age-csv", help="CSV INSEE d'âge médian (optionnel)")
    ap.add_argument("--age-code-col", default="CODGEO")
    ap.add_argument("--age-value-col", default="AGEMED")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "data", "communes_socio.csv"))
    args = ap.parse_args()

    election_file = args.election_file
    tmp = None
    if not election_file:
        tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        tmp.close()
        _download(args.election_url, tmp.name)
        election_file = tmp.name

    gauche = part_gauche_by_commune(election_file)
    print(f"part_gauche calculée pour {len(gauche)} communes.", file=sys.stderr)

    ages = age_by_commune(args.age_csv, args.age_code_col, args.age_value_col) if args.age_csv else {}
    if args.age_csv:
        print(f"age_median pour {len(ages)} communes.", file=sys.stderr)

    codes = sorted(set(gauche) | set(ages))
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["code_insee", "age_median", "part_gauche"])
        for code in codes:
            w.writerow([code, ages.get(code, ""), gauche.get(code, "")])
    print(f"✓ {len(codes)} communes écrites dans {out_path}", file=sys.stderr)

    if tmp:
        os.unlink(tmp.name)


if __name__ == "__main__":
    main()
