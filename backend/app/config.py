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

    # Enrichissement (Lot A)
    enrich_on_search: bool = False
    gpu_api_url: str = "https://apicarto.ign.fr/api/gpu"
    georisques_api_url: str = "https://www.georisques.gouv.fr/api/v1"
    ign_alti_url: str = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"
    navitia_api_key: str = ""
    navitia_url: str = "https://api.navitia.io/v1"
    navitia_origin: str = "Paris"  # ville d'origine pour le temps de trajet train
    ban_reverse_url: str = "https://api-adresse.data.gouv.fr/reverse/"
    hubeau_eau_potable_url: str = "https://hubeau.eaufrance.fr/api/v1/qualite_eau_potable/resultats_dis"
    socio_dataset_path: str = "data/communes_socio.csv"

    @property
    def navitia_configured(self) -> bool:
        return bool(self.navitia_api_key.strip())

    # Source "agences" (newsletters email + sites d'agences)
    anthropic_api_key: str = ""
    extract_model: str = "claude-haiku-4-5"
    agences_config_path: str = "agences.yaml"
    imap_host: str = ""
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    imap_use_ssl: bool = True
    agences_ingest_interval_minutes: int = 30

    @property
    def llm_extract_available(self) -> bool:
        return bool(self.anthropic_api_key.strip())

    @property
    def imap_configured(self) -> bool:
        return bool(self.imap_host.strip() and self.imap_user.strip())

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
