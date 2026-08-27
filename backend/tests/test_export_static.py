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


def test_pepites_gate():
    """Mode « pépites » : filtre le set primaire au seuil, préserve les autres sets."""
    from app.services.export_static import _passes_pepites_gate

    # Bien têtard (membre {1,2}) au-dessus du seuil -> gardé.
    sbs = {"1": {"match_score": 80.0}, "2": {"match_score": 60.0}}
    assert _passes_pepites_gate(sbs, {1, 2}, {1: 78}) is True
    # Même bien sous le seuil sur le set primaire -> écarté.
    assert _passes_pepites_gate({"1": {"match_score": 70.0}}, {1, 2}, {1: 78}) is False
    # Bien Pauline (membre {3}, pas du set primaire 1) -> toujours conservé.
    assert _passes_pepites_gate({"3": {"match_score": 40.0}}, {3}, {1: 78}) is True
    # Membre du set primaire mais non scoré dessus -> écarté (pas une pépite prouvée).
    assert _passes_pepites_gate({"2": {"match_score": 90.0}}, {1, 2}, {1: 78}) is False
    # member vide (rétro-compat "tous sets") + score suffisant -> gardé.
    assert _passes_pepites_gate({"1": {"match_score": 79.0}}, set(), {1: 78}) is True


def test_pepites_gate_plusieurs_sets():
    """La base garde tout le catalogue de chaque set, data.json n'en publie que le haut du
    panier : resserrer un seul set à l'export ferait revenir en bloc celui des autres."""
    from app.services.export_static import _passes_pepites_gate, _seuils_pepites

    seuils = {1: 78.5, 4: 80.0}
    # Chacun jugé sur SON set, pas sur celui de l'autre.
    assert _passes_pepites_gate({"1": {"match_score": 79.0}}, {1, 2}, seuils) is True
    assert _passes_pepites_gate({"4": {"match_score": 79.0}}, {4}, seuils) is False
    assert _passes_pepites_gate({"4": {"match_score": 81.0}}, {4}, seuils) is True
    # Un set non resserré n'est pas concerné.
    assert _passes_pepites_gate({"3": {"match_score": 10.0}}, {3}, seuils) is True
    # L'ancienne écriture reste acceptée et se fond dans la nouvelle.
    assert _seuils_pepites(78.0, 1, None) == {1: 78.0}
    assert _seuils_pepites(None, None, {1: 78.5, 4: 80.0}) == seuils
    assert _seuils_pepites(None, None, None) == {}
