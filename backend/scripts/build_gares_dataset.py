#!/usr/bin/env python3
"""Construit `backend/data/gares_voyageurs.csv` depuis l'open data SNCF.

Deux jeux, aucune clé :

- **`liste-des-gares`** — les 3 279 gares VOYAGEURS de France (nom, commune,
  département, coordonnées). L'ancien `gares.csv` en embarquait 89, choisies à la main :
  un bien à 4 km d'une gare TER y était déclaré « à 40 km d'une gare ».
- **`regularite-mensuelle-tgv-aqst`** — la régularité mensuelle publiée par la SNCF,
  qui porte pour chaque liaison sa **durée moyenne réellement observée**. Filtrée sur les
  départs de Paris, elle donne 51 liaisons et leur temps de trajet mesuré, moyenné sur
  les mois disponibles. C'est ce qui remplace l'estimation « distance / 150 km/h » du
  provider `rail_time` : sur Paris → Grenoble, l'estimation donnait 2h50, la mesure
  donne 3h01 ; sur Paris → Annecy, 2h50 contre 3h47 mesurées.

Les gares desservies directement depuis Paris portent `paris_min` et `paris_gare` ; les
autres n'ont rien dans ces colonnes — **on ne modélise pas un trajet avec correspondance
faute de source mesurée**, et une durée inventée serait exactement le défaut qu'on répare.

Usage : python scripts/build_gares_dataset.py [--out backend/data/gares_voyageurs.csv]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request

API = "https://ressources.data.sncf.com/api/explore/v2.1/catalog/datasets"
_ICI = os.path.dirname(os.path.abspath(__file__))
_DEFAUT = os.path.join(_ICI, "..", "data", "gares_voyageurs.csv")

# Gares hors référentiel français que la SNCF dessert depuis Paris et qui changent
# quelque chose pour un bien FRANÇAIS : Genève est la gare d'arrivée naturelle du
# Chablais et du Genevois, à moins d'une heure de route de Thonon ou d'Annemasse.
_HORS_FRANCE = {
    "GENEVE": ("Genève (Cornavin)", "GENEVE", "SUISSE", 46.2102, 6.1424),
}
# Liaisons AQST qui ne désignent pas une gare (agrégats, destinations lointaines).
_IGNORE = {"ITALIE", "BARCELONA", "FRANCFORT", "STUTTGART", "ZURICH", "LAUSANNE"}
# Le jeu SNCF nomme les gares parisiennes en majuscules abrégées. Les écrire en toutes
# lettres, parce que ce nom s'affiche sur le site : « Paris Lyon » n'est pas une gare.
_GARES_PARIS = {
    "PARIS LYON": "Paris Gare de Lyon", "PARIS MONTPARNASSE": "Paris Montparnasse",
    "PARIS NORD": "Paris Gare du Nord", "PARIS EST": "Paris Gare de l'Est",
    "PARIS AUSTERLITZ": "Paris Austerlitz", "PARIS SAINT LAZARE": "Paris Saint-Lazare",
    "PARIS BERCY": "Paris Bercy", "PARIS VAUGIRARD": "Paris Montparnasse",
}


def _sans_accents(txt: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", txt) if unicodedata.category(c) != "Mn")


def normaliser(nom: str) -> str:
    """Clé de rapprochement entre un libellé du référentiel et un nom AQST.

    « Valence TGV » et « VALENCE ALIXAN TGV » désignent la même gare, « St-Pierre-des-Corps »
    et « ST PIERRE DES CORPS » aussi. On ramène tout en majuscules sans accents, on
    déplie les abréviations, et on enlève la ponctuation.
    """
    txt = _sans_accents(nom).upper()
    txt = re.sub(r"[-'’/().]", " ", txt)
    txt = re.sub(r"\bST\b", "SAINT", txt)
    txt = re.sub(r"\bSTE\b", "SAINTE", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _mots(nom: str) -> set[str]:
    return set(normaliser(nom).split())


def _get(url: str, timeout: int = 180):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def charger_gares() -> list[dict]:
    q = urllib.parse.urlencode({
        "select": "code_uic,libelle,commune,departemen,x_wgs84,y_wgs84",
        "where": 'voyageurs="O"',
    })
    lignes = _get(f"{API}/liste-des-gares/exports/json?{q}")
    gares, vues = [], set()
    for g in lignes:
        lat, lon = g.get("y_wgs84"), g.get("x_wgs84")
        if lat is None or lon is None or not g.get("libelle"):
            continue
        # Le référentiel est indexé par TRONÇON : une même gare y revient une fois par
        # ligne qui la dessert. Sans ce dédoublonnage, Grenoble y figure trois fois.
        cle = g["code_uic"] or f"{g['libelle']}|{round(lat, 4)}"
        if cle in vues:
            continue
        vues.add(cle)
        gares.append({
            "code_uic": g["code_uic"], "nom": g["libelle"],
            "commune": (g.get("commune") or "").title(), "departement": g.get("departemen") or "",
            "lat": round(lat, 6), "lon": round(lon, 6),
        })
    return gares


def charger_liaisons_paris(depuis: str = "2024-01") -> dict[str, tuple[int, str]]:
    """{nom AQST: (durée moyenne en minutes, gare parisienne de départ)}."""
    q = urllib.parse.urlencode({
        "select": "date,gare_depart,gare_arrivee,duree_moyenne",
        "where": f'gare_depart like "PARIS" and date>"{depuis}"',
    })
    lignes = _get(f"{API}/regularite-mensuelle-tgv-aqst/exports/json?{q}")
    par_liaison: dict[str, list] = {}
    for r in lignes:
        if r.get("duree_moyenne") is None:
            continue
        par_liaison.setdefault(r["gare_arrivee"], []).append((r["duree_moyenne"], r["gare_depart"]))
    out = {}
    for arrivee, vals in par_liaison.items():
        if arrivee in _IGNORE:
            continue
        # Plusieurs gares parisiennes desservent parfois la même ville : on garde la
        # liaison la plus RAPIDE, celle qu'un voyageur prendrait.
        duree = round(sum(v[0] for v in vals) / len(vals))
        depart = min(vals, key=lambda v: v[0])[1]
        out[arrivee] = (duree, _GARES_PARIS.get(depart, depart.title()))
    return out


def rapprocher(gares: list[dict], liaisons: dict) -> tuple[dict, list]:
    """Associe chaque liaison AQST à une gare du référentiel. Renvoie (index, non résolues).

    Le rapprochement se fait sur les MOTS, pas sur l'égalité : « VALENCE ALIXAN TGV » et
    « Valence TGV » partagent VALENCE et TGV. On exige que tous les mots du libellé du
    référentiel soient dans le nom AQST, ou l'inverse, et on tranche les ex aequo par le
    nombre de mots communs — sinon « Valence Ville » gagnerait contre « Valence TGV ».
    """
    index, orphelines = {}, []
    for aqst, (duree, depart) in liaisons.items():
        mots_aqst = _mots(aqst)
        candidats = []
        for g in gares:
            mots_g = _mots(g["nom"])
            if mots_g <= mots_aqst or mots_aqst <= mots_g:
                candidats.append((len(mots_g & mots_aqst), -abs(len(mots_g) - len(mots_aqst)), g))
        if not candidats:
            orphelines.append(aqst)
            continue
        meilleur = max(candidats, key=lambda c: (c[0], c[1]))[2]
        index[meilleur["code_uic"]] = (duree, depart)
    return index, orphelines


def ecrire(gares: list[dict], index: dict, chemin: str) -> None:
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["code_uic", "nom", "commune", "departement", "lat", "lon", "paris_min", "paris_gare"])
        for g in sorted(gares, key=lambda g: g["nom"]):
            duree, depart = index.get(g["code_uic"], ("", ""))
            w.writerow([g["code_uic"], g["nom"], g["commune"], g["departement"],
                        g["lat"], g["lon"], duree, depart])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=_DEFAUT)
    args = ap.parse_args(argv)

    gares = charger_gares()
    liaisons = charger_liaisons_paris()
    for cle, (nom, commune, dep, lat, lon) in _HORS_FRANCE.items():
        if cle in liaisons:
            gares.append({"code_uic": f"X{cle}", "nom": nom, "commune": commune,
                          "departement": dep, "lat": lat, "lon": lon})
    index, orphelines = rapprocher(gares, liaisons)

    ecrire(gares, index, args.out)
    print(f"{len(gares)} gares voyageurs · {len(index)}/{len(liaisons)} liaisons Paris rapprochées")
    if orphelines:
        print("  non rapprochées :", ", ".join(sorted(orphelines)))
    print(f"→ {os.path.relpath(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
