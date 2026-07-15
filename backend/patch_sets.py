"""Applique les ajustements de scoring aux définitions de sets dans data/data.json :
- Pauline : near_gare max_km 10->20 (secteur rural, on va à la gare en voiture) ;
            + nouveau critère tension_locative (poids 4, clé du locatif).
À lancer APRÈS l'export de collecte (qui régénère data.json), puis re-seed + re-export.
"""
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PATH = os.path.join(ROOT, "data", "data.json")

data = json.load(open(PATH, encoding="utf-8"))
for s in data.get("sets", []):
    if s.get("name") != "Pauline":
        continue
    prefs = s["preferences"]
    for p in prefs:
        if p.get("kind") == "near_gare":
            p.setdefault("params", {})["max_km"] = 20
    if not any(p.get("kind") == "tension_locative" for p in prefs):
        # inséré juste après la fibre (autre critère d'attractivité locative)
        idx = next((i for i, p in enumerate(prefs) if p.get("kind") == "fiber"), len(prefs) - 1)
        prefs.insert(idx + 1, {
            "kind": "tension_locative",
            "label": "Tension locative (forte = mieux)",
            "weight": 4,
            "params": {},
        })
    print("Pauline preferences ->")
    for p in prefs:
        print("  ", p.get("kind"), "w", p.get("weight"), p.get("params"))

json.dump(data, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("data.json mis à jour.")
