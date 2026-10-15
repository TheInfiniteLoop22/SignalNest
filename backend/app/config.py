from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+asyncpg://signalnest:change-me@localhost:5432/signalnest"
    worker_id: str = "cli"
    # SSRF guard (docs/09 section 3). The CIDR override is for dev/test only; empty in prod.
    allowed_ports: str = "80,443,8000-8999"
    ssrf_allow_private_cidrs: str = ""
    allowed_hosts: str = ""
    # Until auth lands in M6, every request acts as this seeded user.
    dev_user_email: str = "dev@signalnest.local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
