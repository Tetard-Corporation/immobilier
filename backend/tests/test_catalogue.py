"""La sauvegarde du catalogue : ce qui part dans git, et ce qu'on sait en relire.

La base SQLite pèse 108 Mo — au-dessus de la limite de 100 Mo par fichier de GitHub — et
`data.json` ne contient que les biens publiés (612 sur 7 540). Ce dump est donc le seul
endroit d'où le catalogue complet peut revenir.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def db():
    """Session sur la DB temporaire de la suite, nettoyée après le test : la base est
    partagée entre les fichiers de tests, un bien oublié fausserait le voisin."""
    from app.db import SessionLocal
    from app.models import Listing

    session = SessionLocal()
    crees: list[int] = []
    yield session, crees
    for pk in crees:
        obj = session.get(Listing, pk)
        if obj is not None:
            session.delete(obj)
    session.commit()
    session.close()


def _bien(db, **kw):
    from app.models import Listing

    session, crees = db
    base = dict(source="catalogue-test", external_id="1", type_bien="maison", prix=200000.0,
                commune="Beaufort", raw={"photos": ["https://img.test/1.jpg"]})
    base.update(kw)
    row = Listing(**base)
    session.add(row)
    session.commit()
    crees.append(row.id)
    return row


def _lignes(chemin) -> list[dict]:
    """Les seules lignes du test : la DB de la suite peut contenir d'autres biens."""
    return [json.loads(x) for x in chemin.read_text(encoding="utf-8").splitlines()
            if json.loads(x).get("source") == "catalogue-test"]


def test_le_dump_garde_le_catalogue_et_jette_le_payload_brut(db, tmp_path):
    from app.services.export_static import dump_catalogue

    _bien(db, external_id="a", nb_chambres=3, features=["eau"], set_ids=[1])
    chemin = tmp_path / "catalogue.jsonl"
    dump_catalogue(db[0], str(chemin))

    ligne = _lignes(chemin)[0]
    # Le catalogue est là...
    assert ligne["commune"] == "Beaufort" and ligne["nb_chambres"] == 3
    assert ligne["features"] == ["eau"] and ligne["set_ids"] == [1]
    # ... les URLs de photos aussi (sans quoi une base restaurée ne pourrait plus
    # télécharger les images des biens jamais publiés)...
    assert ligne["photo_urls"] == ["https://img.test/1.jpg"]
    # ... et les 64 Mo de payload brut, non.
    assert "raw" not in ligne and "score_details" not in ligne


def test_le_dump_est_trie_pour_que_le_diff_git_soit_lisible(db, tmp_path):
    from app.services.export_static import dump_catalogue

    for eid in ("c", "a", "b"):
        _bien(db, external_id=eid)
    chemin = tmp_path / "catalogue.jsonl"
    dump_catalogue(db[0], str(chemin))
    assert [x["external_id"] for x in _lignes(chemin)] == ["a", "b", "c"]
    # Clés triées et ordre stable : deux exports qui ne changent rien produisent le même
    # octet, donc aucun diff git parasite.
    premiere = chemin.read_text(encoding="utf-8")
    dump_catalogue(db[0], str(chemin))
    assert chemin.read_text(encoding="utf-8") == premiere


def test_les_dates_repassent_en_datetime_a_la_restauration(db, tmp_path):
    """Écrites en ISO dans le dump, elles feraient échouer SQLAlchemy telles quelles."""
    from datetime import datetime

    from app.seed import _DATES
    from app.services.export_static import dump_catalogue

    _bien(db, external_id="d")
    chemin = tmp_path / "catalogue.jsonl"
    dump_catalogue(db[0], str(chemin))
    ligne = _lignes(chemin)[0]
    for champ in _DATES:
        assert isinstance(ligne[champ], str)
        assert isinstance(datetime.fromisoformat(ligne[champ]), datetime)


def test_une_ligne_illisible_ne_perd_pas_les_autres(tmp_path):
    """Une sauvegarde tronquée doit restaurer ce qu'elle contient, pas rien."""
    from app.seed import _biens_du_catalogue

    f = tmp_path / "catalogue.jsonl"
    f.write_text('{"source": "a", "external_id": "1"}\n'
                 '{tronqu\n'
                 '{"source": "b", "external_id": "2"}\n', encoding="utf-8")
    assert [b["source"] for b in _biens_du_catalogue(str(f))] == ["a", "b"]


def test_l_export_n_ecrit_aucune_sauvegarde_sans_qu_on_la_demande(client, tmp_path):
    """Le garde-fou du défaut qui a fait perdre la sauvegarde : avec un chemin par défaut
    pointant sur le dépôt, `pytest` écrasait les 7 540 biens du catalogue par les huit de
    sa base temporaire. Le chemin est désormais exigé, et l'export ne le pose que si la
    CLI le lui donne."""
    import inspect

    from app.db import SessionLocal
    from app.services.export_static import dump_catalogue, export_to_dir

    # Aucune valeur par défaut : appeler le dump sans chemin est une erreur, pas un écrasement.
    assert inspect.signature(dump_catalogue).parameters["chemin"].default is inspect.Parameter.empty

    stats = export_to_dir(SessionLocal(), str(tmp_path / "out"), download_photos=False)
    assert "catalogue" not in stats
    assert not list(tmp_path.rglob("catalogue.jsonl"))

    # Et avec un chemin, il l'écrit là où on le lui dit.
    cible = tmp_path / "ailleurs" / "catalogue.jsonl"
    stats = export_to_dir(SessionLocal(), str(tmp_path / "out"), download_photos=False,
                          catalogue=str(cible))
    assert stats["catalogue"]["biens"] >= 0 and cible.exists()
