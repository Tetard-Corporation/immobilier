"""Tests d'API de bout en bout via la source mock (sans réseau)."""


def test_health_et_sources(client):
    assert client.get("/api/health").json()["status"] == "ok"
    names = {s["name"] for s in client.get("/api/sources").json()}
    assert {"pappers", "bienici", "mock"} <= names


def test_filters_schema(client):
    schema = client.get("/api/filters/schema").json()
    groups = {g["key"] for g in schema["groups"]}
    assert {"localisation", "prix", "surfaces", "bien", "etat"} <= groups


def test_search_mock_filtre(client):
    r = client.post(
        "/api/search?source=mock",
        json={"property_types": ["terrain"], "departement": "GIRONDE"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["source"] == "mock"
    assert all(item["type_bien"] == "terrain" for item in data["results"])
    assert all(item["departement"] == "GIRONDE" for item in data["results"])


def test_filter_set_crud(client):
    created = client.post(
        "/api/filter-sets",
        json={"name": "Terrains 33", "criteria": {"departement": "GIRONDE", "prix_max": 100000}},
    ).json()
    fs_id = created["id"]
    assert created["criteria"]["prix_max"] == 100000

    listed = client.get("/api/filter-sets").json()
    assert any(f["id"] == fs_id for f in listed)

    client.put(
        f"/api/filter-sets/{fs_id}",
        json={"name": "Terrains 33 maj", "criteria": {"departement": "GIRONDE"}},
    )
    assert client.get(f"/api/filter-sets/{fs_id}").json()["name"] == "Terrains 33 maj"

    assert client.delete(f"/api/filter-sets/{fs_id}").status_code == 204
    assert client.get(f"/api/filter-sets/{fs_id}").status_code == 404


def test_saved_search_nouveautes(client):
    ss = client.post(
        "/api/saved-searches",
        json={
            "name": "Terrains Gironde",
            "source": "mock",
            "criteria": {"departement": "GIRONDE", "property_types": ["terrain"]},
            "frequency_minutes": 60,
        },
    ).json()
    ss_id = ss["id"]

    # 1er run : tout est nouveau
    run1 = client.post(f"/api/saved-searches/{ss_id}/run").json()
    assert run1["nb_results"] >= 1
    assert run1["nb_new"] == run1["nb_results"]

    # 2e run : plus aucune nouveauté
    run2 = client.post(f"/api/saved-searches/{ss_id}/run").json()
    assert run2["nb_new"] == 0

    assert client.get(f"/api/saved-searches/{ss_id}").json()["nb_new"] == run1["nb_new"]

    # Résultats "nouveautés uniquement"
    new_only = client.get(f"/api/saved-searches/{ss_id}/results?only_new=true").json()
    assert len(new_only) == run1["nb_new"]
    assert all(item["is_new"] for item in new_only)

    # Marquer comme vu -> plus de nouveautés
    assert client.post(f"/api/saved-searches/{ss_id}/mark-seen").json()["updated"] == run1["nb_new"]
    assert client.get(f"/api/saved-searches/{ss_id}").json()["nb_new"] == 0

    # Historique des runs
    assert len(client.get(f"/api/saved-searches/{ss_id}/runs").json()) == 2
