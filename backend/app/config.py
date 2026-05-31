"""Configuration de l'application, lue depuis l'environnement / un fichier .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Pappers Immobilier
    pappers_api_key: str = ""
    pappers_base_url: str = "https://api-immobilier.pappers.fr/v1"

    # Base de données
    database_url: str = "sqlite:///./immobilier.db"

    # Scheduler
    scheduler_enabled: bool = True
    scheduler_tick_seconds: int = 300

    # Maîtrise des crédits / pagination
    default_bases: str = "ventes"
    max_pages_per_run: int = 5
    cache_ttl_seconds: int = 3600

    # Scraping
    proxy_url: str = ""
    scraper_rate_limit_ms: int = 2000

    # Divers
    http_timeout_seconds: int = 20
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def default_bases_list(self) -> list[str]:
        return [b.strip() for b in self.default_bases.split(",") if b.strip()]

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def pappers_configured(self) -> bool:
        return bool(self.pappers_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
