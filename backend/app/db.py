"""Connexion base de données et session SQLAlchemy."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    url = settings.database_url
    # check_same_thread requis pour SQLite quand utilisé par le scheduler (autre thread).
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Crée les tables. (Alembic pourra prendre le relais plus tard.)"""
    from . import models  # noqa: F401  -- enregistre les modèles sur Base.metadata

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """Dépendance FastAPI : fournit une session par requête."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
