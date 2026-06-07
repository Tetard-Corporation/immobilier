"""Tests des sous-sets de filtres (héritage) et des favoris."""

from app.services.filtersets import merge_criteria


def test_merge_surcharge_champs_simples():
    parent = {"property_types": ["maison", "immeuble"], "prix_max": 600000}
    child = {"property_types": ["maison"]}  # Léo : jardin -> maison seule
    merged = merge_criteria(parent, child)
    assert merged["property_types"] == ["maison"]
    assert merged["prix_max"] == 600000  # hérité


def test_merge_preferences_par_kind():
    parent = {"preferences": [
        {"kind": "has_terrain", "weight": 1},
        {"kind": "nature_exception", "weight": 2},
    ]}
    child = {"preferences": [
        {"kind": "has_terrain", "weight": 3, "params": {"min_surface": 1500}},  # surcharge
        {"kind": "feature", "weight": 2, "params": {"name": "vue"}},            # nouvelle
    ]}
    merged = merge_criteria(parent, child)
    prefs = {p["kind"]: p for p in merged["preferences"]}
    assert prefs["has_terrain"]["weight"] == 3
    assert prefs["has_terrain"]["params"]["min_surface"] == 1500
    assert prefs["nature_exception"]["weight"] == 2  # conservée du parent
    assert prefs["feature"]["params"]["name"] == "vue"  # ajoutée


def test_child_sans_preferences_garde_celles_du_parent():
    parent = {"preferences": [{"kind": "has_terrain", "weight": 1}]}
    merged = merge_criteria(parent, {"prix_max": 500000})
    assert merged["preferences"] == parent["preferences"]


def test_subset_resolved_via_api(client):
    parent = client.post("/api/filter-sets", json={
        "name": "têtard", "criteria": {"property_types": ["maison"],
        "preferences": [{"kind": "has_terrain", "weight": 1}]}}).json()
    child = client.post("/api/filter-sets", json={
        "name": "Léo", "parent_id": parent["id"],
        "criteria": {"preferences": [
            {"kind": "has_terrain", "weight": 3, "params": {"min_surface": 1500}},
            {"kind": "feature", "weight": 2, "params": {"name": "vue"}}]}}).json()
    assert child["parent_id"] == parent["id"]
    resolved = client.get(f"/api/filter-sets/{child['id']}/resolved").json()["resolved_criteria"]
    prefs = {p["kind"]: p for p in resolved["preferences"]}
    assert prefs["has_terrain"]["weight"] == 3
    assert prefs["feature"]["params"]["name"] == "vue"
    # liste des enfants d'un parent
    kids = client.get(f"/api/filter-sets?parent_id={parent['id']}").json()
    assert any(k["id"] == child["id"] for k in kids)


def test_favoris_crud(client):
    # crée un bien via une recherche mock
    res = client.post("/api/search?source=mock", json={"property_types": ["terrain"]}).json()
    lid = res["results"][0]["id"]
    saved = client.post("/api/saved-listings", json={"listing_id": lid, "note": "bien isolé"}).json()
    assert saved["note"] == "bien isolé" and saved["snapshot"]["id"] == lid
    # idempotent : re-sauver met à jour la note
    again = client.post("/api/saved-listings", json={"listing_id": lid, "note": "coup de cœur"}).json()
    assert again["id"] == saved["id"] and again["note"] == "coup de cœur"
    # listing
    assert any(s["id"] == saved["id"] for s in client.get("/api/saved-listings").json())
    # suppression
    assert client.delete(f"/api/saved-listings/{saved['id']}").status_code == 204
