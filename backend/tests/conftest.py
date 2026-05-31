"""Configuration pytest : DB temporaire isolée, scheduler désactivé, client API."""

from __future__ import annotations

import os
import tempfile

import pytest

# Doit être défini AVANT tout import de l'app (engine créé à l'import de app.db).
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["PAPPERS_API_KEY"] = ""
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"


@pytest.fixture(scope="session", autouse=True)
def _init_database():
    from app.db import init_db

    init_db()
    yield
    os.close(_DB_FD)
    os.unlink(_DB_PATH)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
