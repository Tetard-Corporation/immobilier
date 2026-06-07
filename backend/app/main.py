"""Point d'entrée FastAPI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import init_db
from .routers import (
    filter_sets,
    listings,
    saved_listings,
    saved_searches,
    search,
    sources,
)
from .scheduler import shutdown_scheduler, start_scheduler

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Moteur de prospection foncière/immobilière",
        version="0.1.0",
        description="Recherche multi-critères sur données Pappers Immobilier, "
        "jeux de filtres et recherches fréquentes avec détection de nouveautés.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api = "/api"
    app.include_router(sources.router, prefix=api)
    app.include_router(search.router, prefix=api)
    app.include_router(filter_sets.router, prefix=api)
    app.include_router(saved_searches.router, prefix=api)
    app.include_router(saved_listings.router, prefix=api)
    app.include_router(listings.router, prefix=api)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "pappers_configured": settings.pappers_configured}

    return app


app = create_app()
