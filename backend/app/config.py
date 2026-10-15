from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+asyncpg://signalnest:change-me@localhost:5432/signalnest"
    worker_id: str = "cli"
    # Until auth lands in M6, every request acts as this seeded user.
    dev_user_email: str = "dev@signalnest.local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
