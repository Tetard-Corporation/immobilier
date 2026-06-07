"""Test de l'export statique (dataset JSON) sans accès réseau (download_photos=False)."""

from __future__ import annotations

import json


def _seed(client):
    """Crée un set têtard + un bien réel via une recherche mock (persiste un Listing)."""
    client.post("/api/filter-sets", json={
        "name": "têtard-test",
        "criteria": {"preferences": [
            {"kind": "budget", "weight": 2, "params": {"budget_max": 300000}, "label": "Budget"},
            {"kind": "chambres_min", "weight": 1, "params": {"min": 3}, "label": "≥3 ch"},
        ]},
    })
    client.post("/api/search?source=mock&sort=score", json={"property_types": ["maison"]})


def test_export_build_dataset(client, tmp_path):
    _seed(client)
    from app.db import SessionLocal
    from app.services.export_static import build_dataset, export_to_dir

    db = SessionLocal()
    data = build_dataset(db, download_photos=False)

    assert {"generated_at", "sets", "biens", "searches", "stats"} <= data.keys()
    assert data["stats"]["n_biens"] == len(data["biens"])
    # un set avec préférences est exporté avec ses critères
    sets_named = {s["name"]: s for s in data["sets"]}
    assert "têtard-test" in sets_named
    assert len(sets_named["têtard-test"]["preferences"]) == 2

    # chaque bien porte un match recalculé pour le set, et la liste photos (vide ici)
    for b in data["biens"]:
        assert "scores_by_set" in b and "photos" in b
        assert b["photos"] == []  # pas de téléchargement réseau
    # la recherche mock a bien été tracée dans l'historique
    assert data["stats"]["n_searches"] >= 1

    # écriture sur disque
    stats = export_to_dir(db, str(tmp_path / "data"), download_photos=False)
    written = json.loads((tmp_path / "data" / "data.json").read_text(encoding="utf-8"))
    assert written["stats"] == stats
