"""Test de l'auto-migration légère (ajout des colonnes manquantes)."""

from sqlalchemy import create_engine, inspect, text

from app.db import ensure_columns


def test_ensure_columns_ajoute_les_manquantes(tmp_path):
    db = tmp_path / "old.db"
    eng = create_engine(f"sqlite:///{db}")
    # Simule un ancien fichier : table 'listings' réduite à quelques colonnes.
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE listings (id INTEGER PRIMARY KEY, source TEXT, external_id TEXT)"))

    added = ensure_columns(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("listings")}
    # Colonnes ajoutées au fil des lots, désormais présentes.
    assert {"score", "altitude", "features", "constructible", "ecart_prix_pct", "nb_chambres"} <= cols
    assert any("listings." in a for a in added)
