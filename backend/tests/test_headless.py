"""Tests de la couche headless : cache cookie Datadome + repli du connecteur (offline).

Le pilotage du navigateur (Playwright) n'est pas testé ici — seulement le cache disque
et la résolution du cookie côté connecteur Leboncoin.
"""

import time

from app.config import Settings
from app.sources import headless
from app.sources.leboncoin import LeboncoinSource
from app.sources.seloger import SeLogerSource


def _settings(tmp_path, **kw) -> Settings:
    defaults = {"leboncoin_datadome": "", "proxy_url": ""}
    return Settings(headless_state_dir=str(tmp_path), **{**defaults, **kw})


def test_store_puis_lecture(tmp_path):
    s = _settings(tmp_path)
    headless.store_datadome("leboncoin.fr", "COOKIEVAL", "UA/1.0", settings=s)
    entry = headless.get_cached_datadome("leboncoin.fr", settings=s)
    assert entry is not None
    assert entry["cookie"] == "COOKIEVAL"
    assert entry["ua"] == "UA/1.0"


def test_absent_renvoie_none(tmp_path):
    assert headless.get_cached_datadome("leboncoin.fr", settings=_settings(tmp_path)) is None


def test_cookie_perime_ignore(tmp_path):
    s = _settings(tmp_path, datadome_max_age_minutes=1)
    headless.store_datadome("leboncoin.fr", "OLD", "UA", settings=s)
    # Antidate l'entrée au-delà de la fenêtre de fraîcheur.
    path = headless._cache_path(s)
    data = headless._read_cache(s)
    data["leboncoin.fr"]["ts"] = time.time() - 120
    path.write_text(__import__("json").dumps(data), "utf-8")
    assert headless.get_cached_datadome("leboncoin.fr", settings=s) is None


def test_leboncoin_utilise_le_cookie_recolte(tmp_path):
    s = _settings(tmp_path)
    headless.store_datadome("leboncoin.fr", "HARVESTED", "Mozilla/5.0 Desktop", settings=s)
    src = LeboncoinSource(settings=s)
    assert src.available is True                       # cookie récolté -> source utilisable
    h = src._headers()
    assert h["Cookie"] == "datadome=HARVESTED"
    assert h["User-Agent"] == "Mozilla/5.0 Desktop"    # UA cohérent avec le cookie récolté


def test_cookie_configure_prioritaire_sur_le_cache(tmp_path):
    s = _settings(tmp_path, leboncoin_datadome="CONFIGURED")
    headless.store_datadome("leboncoin.fr", "HARVESTED", "Mozilla/5.0 Desktop", settings=s)
    src = LeboncoinSource(settings=s)
    h = src._headers()
    assert h["Cookie"] == "datadome=CONFIGURED"        # explicite > récolté
    assert "Mozilla" not in h["User-Agent"]            # UA mobile par défaut conservé


def test_indisponible_sans_cookie_ni_proxy(tmp_path):
    src = LeboncoinSource(settings=_settings(tmp_path))
    assert src.available is False
    assert "Cookie" not in src._headers()


# --- SeLoger : mêmes mécanismes (cookie récolté + UA cohérent, override configuré) ---

def test_seloger_utilise_le_cookie_recolte(tmp_path):
    s = _settings(tmp_path)
    headless.store_datadome("seloger.com", "SLG", "Mozilla/5.0 Desktop", settings=s)
    h = SeLogerSource(settings=s)._headers()
    assert h["Cookie"] == "datadome=SLG"
    assert h["User-Agent"] == "Mozilla/5.0 Desktop"


def test_seloger_cookie_configure_prioritaire(tmp_path):
    s = _settings(tmp_path, seloger_datadome="CONF")
    headless.store_datadome("seloger.com", "SLG", "Mozilla/5.0 Desktop", settings=s)
    h = SeLogerSource(settings=s)._headers()
    assert h["Cookie"] == "datadome=CONF"
    assert "User-Agent" not in h            # UA par défaut du client conservé


def test_seloger_sans_cookie_pas_d_entete(tmp_path):
    assert SeLogerSource(settings=_settings(tmp_path))._headers() is None
