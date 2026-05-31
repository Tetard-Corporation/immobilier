"""Recherches fréquentes : exécution périodique et détection des nouveautés.

Une "nouveauté" = une annonce (external_id) jamais vue par cette recherche.
Le diff isole ici la logique : un notifier (email/push) pourra s'y brancher plus tard.
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..models import SavedSearch, SearchRun, SeenListing
from ..schemas import SearchCriteria
from ..sources import resolve_source
from .search import upsert_listing


def effective_criteria(db: Session, ss: SavedSearch) -> SearchCriteria:
    """Critères effectifs d'une recherche : ceux du FilterSet rattaché, sinon inline."""
    data = ss.criteria or {}
    if ss.filter_set_id and ss.filter_set is not None and ss.filter_set.criteria:
        data = ss.filter_set.criteria
    return SearchCriteria.model_validate(data)


def count_new(db: Session, saved_search_id: int) -> int:
    stmt = select(func.count(SeenListing.id)).where(
        SeenListing.saved_search_id == saved_search_id, SeenListing.is_new.is_(True)
    )
    return int(db.execute(stmt).scalar_one())


def mark_all_seen(db: Session, saved_search_id: int) -> int:
    """Marque toutes les nouveautés comme vues. Renvoie le nombre mis à jour."""
    result = db.execute(
        update(SeenListing)
        .where(SeenListing.saved_search_id == saved_search_id, SeenListing.is_new.is_(True))
        .values(is_new=False)
    )
    db.commit()
    return int(result.rowcount or 0)


def run_saved_search(db: Session, ss: SavedSearch) -> SearchRun:
    """Exécute une recherche fréquente, détecte les nouveautés, enregistre le run."""
    run = SearchRun(saved_search_id=ss.id)
    try:
        source = resolve_source(ss.source)
        criteria = effective_criteria(db, ss)
        result = source.search(criteria)

        existing = set(
            db.execute(
                select(SeenListing.external_id).where(SeenListing.saved_search_id == ss.id)
            ).scalars()
        )

        nb_new = 0
        for item in result.items:
            row = upsert_listing(db, item)
            if item.external_id not in existing:
                db.add(
                    SeenListing(
                        saved_search_id=ss.id,
                        listing_id=row.id,
                        source=item.source,
                        external_id=item.external_id,
                        is_new=True,
                    )
                )
                existing.add(item.external_id)
                nb_new += 1

        run.nb_results = result.total if result.total is not None else len(result.items)
        run.nb_new = nb_new
        run.credits_estimes = result.credits_estimes
        ss.last_run_at = run.ran_at
    except Exception as exc:  # on persiste l'erreur plutôt que de planter le scheduler
        run.error = str(exc)[:2000]

    db.add(run)
    db.commit()
    db.refresh(run)
    return run
