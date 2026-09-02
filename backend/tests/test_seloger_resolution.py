"""Résolution des communes en placeId SeLoger : les trois états, et le blocage.

Le défaut corrigé : `place_id` rendait `None` aussi bien pour « SeLoger n'indexe pas
cette commune » que pour « nous sommes bloqués », et n'en mémorisait que le premier.
Une résolution lancée avec un cookie mort a ainsi déclaré 1 034 communes « sans
placeId » — un symptôme de blocage présenté comme un fait de données — pendant que le
cache n'en contenait qu'une seule. Zéro bien collecté, et rien dans le log pour le dire.
"""

import httpx
import pytest

from app.sources.scraper import ScraperBlocked
from app.sources import seloger
from app.sources.seloger import ABSENT, SeLogerSource


@pytest.fixture
def src(tmp_path, monkeypatch):
    # Le cache est un chemin de module : sans cette redirection, les tests liraient (et
    # écriraient) le vrai data/seloger_places.json.
    monkeypatch.setattr(seloger, "_PLACES_CACHE", str(tmp_path / "places.json"))
    return SeLogerSource()


def _repondre(monkeypatch, src, html: str | None = None, exc: Exception | None = None):
    def _get(path, **kw):
        if exc is not None:
            raise exc
        return httpx.Response(200, text=html or "", request=httpx.Request("GET", "http://x"))
    monkeypatch.setattr(src, "_get", _get)


def test_commune_jamais_demandee_est_absente(src):
    assert src.place_id_en_cache("Jarsy", "73") is ABSENT


def test_commune_non_indexee_est_memorisee(src, monkeypatch):
    # SeLoger répond, mais sa page ne porte aucun placeId : la commune n'est pas indexée.
    # Ce résultat-là se mémorise — inutile de la redemander à chaque collecte.
    _repondre(monkeypatch, src, html="<html>rien</html>")
    assert src.place_id("Zzz-sur-Néant", "73") is None
    assert src.place_id_en_cache("Zzz-sur-Néant", "73") is None   # mémorisé, pas ABSENT


def test_un_blocage_remonte_et_ne_se_memorise_pas(src, monkeypatch):
    # Le cœur du correctif : un blocage n'est pas une réponse. Il doit interrompre la
    # résolution, pas se déguiser en « commune inconnue » et polluer le cache.
    _repondre(monkeypatch, src, exc=ScraperBlocked("HTTP 403"))
    with pytest.raises(ScraperBlocked):
        src.place_id("Jarsy", "73")
    assert src.place_id_en_cache("Jarsy", "73") is ABSENT


def test_une_erreur_http_ordinaire_reste_absorbee(src, monkeypatch):
    # Une panne transitoire n'est ni un blocage ni une absence : on rend None sans
    # mémoriser, la prochaine passe réessaiera.
    _repondre(monkeypatch, src, exc=httpx.ConnectError("boom"))
    assert src.place_id("Jarsy", "73") is None
    assert src.place_id_en_cache("Jarsy", "73") is ABSENT
