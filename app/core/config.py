"""
Application Configuration — Pydantic Settings

WHY PYDANTIC SETTINGS?
  In production systems, configuration should NEVER be hardcoded.
  pydantic-settings reads from environment variables (or .env files),
  validates types automatically, and provides a single source of truth.

  This is the "12-Factor App" methodology — config comes from the environment,
  making the same codebase deployable across dev/staging/prod.

WHY A SINGLE SETTINGS OBJECT?
  Dependency injection in FastAPI lets us inject this anywhere.
  Any service needing config asks for `Settings` — no global imports,
  no scattered os.getenv() calls, fully testable with overrides.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # --- Database ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/url_shortener"

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Application ---
    base_url: str = "http://localhost:8000"
    app_env: str = "development"
    debug: bool = True

    # --- Rate Limiting (Token Bucket) ---
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # --- Cache ---
    cache_ttl_seconds: int = 3600

    # --- URL Settings ---
    short_code_length: int = 7
    default_expiry_minutes: int = 0  # 0 = no expiry

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.app_env == "production"


@lru_cache()
def get_settings() -> Settings:
    """
    Cached settings factory.

    WHY LRU_CACHE?
      Settings are read once from env and reused. This avoids
      re-parsing .env on every request — a micro-optimization
      that matters at scale (thousands of requests/sec).
    """
    return Settings()
