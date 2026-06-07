#!/usr/bin/env python3
"""Calcule l'âge médian par commune à partir d'un fichier INSEE de structure par âge.

INSEE ne publie pas d'« âge médian par commune » prêt à l'emploi : on l'estime à partir
des **grandes tranches d'âge** (recensement) par interpolation linéaire dans la tranche
qui contient la médiane.

Fichier attendu (INSEE « base-cc-evol-struct-pop ») : CSV (séparateur `;` ou `,`) avec
une colonne code commune (CODGEO) et 7 colonnes de population par tranche, dont les noms
se terminent par : POP0014, POP1529, POP3044, POP4559, POP6074, POP7589, POP90P
(préfixe d'année variable, ex. `P21_POP0014` — détecté automatiquement).

Source : INSEE, « Évolution et structure de la population » (séries communales) —
https://www.insee.fr/fr/statistiques (rechercher « base-cc-evol-struct-pop »).

Exemple
-------
    python scripts/build_age_dataset.py --insee-file base-cc-evol-struct-pop-2021.CSV
    # puis :
    python scripts/build_socio_dataset.py --age-csv data/age_median.csv \
        --age-code-col code_insee --age-value-col age_median
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

# (suffixe de colonne, borne basse, largeur de la tranche en années)
BRACKETS = [
    ("POP0014", 0, 15),
    ("POP1529", 15, 15),
    ("POP3044", 30, 15),
    ("POP4559", 45, 15),
    ("POP6074", 60, 15),
    ("POP7589", 75, 15),
    ("POP90P", 90, 15),
]


def median_age_from_brackets(counts: list[float]) -> float | None:
    """Âge médian estimé par interpolation linéaire dans la tranche médiane."""
    total = sum(counts)
    if total <= 0:
        return None
    half = total / 2
    cumul = 0.0
    for (_, low, width), n in zip(BRACKETS, counts):
        if cumul + n >= half:
            if n <= 0:
                return float(low)
            return round(low + (half - cumul) / n * width, 1)
        cumul += n
    return float(BRACKETS[-1][1])


def _num(v: str) -> float:
    try:
        return float((v or "").replace(",", ".").replace(" ", "").replace(" ", ""))
    except ValueError:
        return 0.0


def _resolve_columns(header: list[str]) -> dict[str, str]:
    cols = {}
    for suffix, _, _ in BRACKETS:
        match = next((h for h in header if h.upper().replace("_", "").endswith(suffix)), None)
        if match is None:
            raise SystemExit(f"Colonne introuvable pour la tranche {suffix} (vérifier le fichier INSEE).")
        cols[suffix] = match
    return cols


def main() -> None:
    ap = argparse.ArgumentParser(description="Estime l'âge médian par commune (INSEE).")
    ap.add_argument("--insee-file", required=True, help="CSV INSEE structure par âge")
    ap.add_argument("--code-col", default="CODGEO", help="Colonne du code commune")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "data", "age_median.csv"))
    args = ap.parse_args()

    with open(args.insee_file, encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        delim = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(fh, delimiter=delim)
        cols = _resolve_columns(reader.fieldnames or [])
        if args.code_col not in (reader.fieldnames or []):
            raise SystemExit(f"Colonne code commune '{args.code_col}' absente. Colonnes : {reader.fieldnames}")

        rows = []
        for r in reader:
            counts = [_num(r.get(cols[suffix], "")) for suffix, _, _ in BRACKETS]
            age = median_age_from_brackets(counts)
            code = (r.get(args.code_col) or "").strip()
            if code and age is not None:
                rows.append((code, age))

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["code_insee", "age_median"])
        w.writerows(rows)
    print(f"✓ âge médian estimé pour {len(rows)} communes -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
