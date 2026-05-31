"""Connexion base de données et session SQLAlchemy."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
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


def ensure_columns(target_engine=None) -> list[str]:
    """Auto-migration légère : ajoute les colonnes manquantes des modèles.

    `create_all` ne crée que les tables absentes, pas les colonnes ajoutées ensuite.
    On comble l'écart par des `ALTER TABLE ADD COLUMN` (colonnes nullable) pour qu'un
    ancien fichier SQLite reste utilisable après évolution du schéma. Renvoie la liste
    des colonnes ajoutées.
    """
    target_engine = target_engine or engine
    insp = inspect(target_engine)
    tables = set(insp.get_table_names())
    added: list[str] = []
    with target_engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                coltype = col.type.compile(dialect=target_engine.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {coltype}'))
                added.append(f"{table.name}.{col.name}")
    return added


def init_db() -> None:
    """Crée les tables manquantes puis comble les colonnes manquantes (Alembic à terme)."""
    from . import models  # noqa: F401  -- enregistre les modèles sur Base.metadata

    Base.metadata.create_all(bind=engine)
    ensure_columns()


def get_db() -> Iterator[Session]:
    """Dépendance FastAPI : fournit une session par requête."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
