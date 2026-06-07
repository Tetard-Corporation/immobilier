"""Scheduler des recherches fréquentes (APScheduler, in-process)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal
from .models import SavedSearch
from .services.alerts import run_saved_search

logger = logging.getLogger("immobilier.scheduler")

_scheduler: BackgroundScheduler | None = None


def _is_due(ss: SavedSearch, now: datetime) -> bool:
    if not ss.enabled or ss.frequency_minutes <= 0:
        return False
    if ss.last_run_at is None:
        return True
    last = ss.last_run_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last >= timedelta(minutes=ss.frequency_minutes)


def tick() -> None:
    """Passage du scheduler : exécute les recherches dont la fréquence est échue."""
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        searches = db.execute(select(SavedSearch).where(SavedSearch.enabled.is_(True))).scalars()
        for ss in list(searches):
            if _is_due(ss, now):
                logger.info("Exécution de la recherche fréquente #%s (%s)", ss.id, ss.name)
                run_saved_search(db, ss)
    finally:
        db.close()


def ingest_agences_tick() -> None:
    """Passage périodique : ingestion des newsletters/sites d'agences."""
    from .services.agences_ingest import ingest

    db = SessionLocal()
    try:
        ingest(db)
    except Exception:  # ne jamais faire tomber le scheduler
        logger.exception("Échec de l'ingestion agences.")
    finally:
        db.close()


def _agences_enabled() -> bool:
    from .agences_config import load_agences_config

    settings = get_settings()
    if settings.imap_configured:
        return True
    return bool(load_agences_config(settings.agences_config_path).all_site_urls)


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("Scheduler désactivé (SCHEDULER_ENABLED=false).")
        return None
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        tick,
        "interval",
        seconds=settings.scheduler_tick_seconds,
        id="saved_searches_tick",
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
    )
    if _agences_enabled():
        _scheduler.add_job(
            ingest_agences_tick,
            "interval",
            minutes=max(settings.agences_ingest_interval_minutes, 1),
            id="agences_ingest_tick",
            next_run_time=datetime.now(timezone.utc),
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "Ingestion agences activée (toutes les %s min).",
            settings.agences_ingest_interval_minutes,
        )
    _scheduler.start()
    logger.info("Scheduler démarré (tick = %ss).", settings.scheduler_tick_seconds)
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
