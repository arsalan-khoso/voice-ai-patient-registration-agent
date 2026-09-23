"""Runtime configuration. Everything comes from environment variables (12-factor);
no secrets live in source control. See .env.example for the full list."""
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"  # "production" turns on strict startup checks
    database_url: str = "sqlite:///./data/patients.db"
    log_level: str = "INFO"
    log_pii: bool = True  # False masks phone/email/DOB in the payload logs
    seed_demo_data: bool = False

    # Optional shared secret for the REST API (X-API-Key header). Empty = open API.
    api_key: str = ""
    # Shared secret Vapi sends on every webhook call (X-Vapi-Secret header).
    vapi_webhook_secret: str = ""

    @property
    def sqlalchemy_url(self) -> str:
        """Managed hosts hand out postgres:// or postgresql:// URLs; make them use psycopg 3."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @model_validator(mode="after")
    def _production_requires_secrets(self):
        if self.is_production and not self.vapi_webhook_secret:
            raise ValueError("VAPI_WEBHOOK_SECRET must be set when ENVIRONMENT=production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
