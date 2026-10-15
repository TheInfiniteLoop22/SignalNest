from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+asyncpg://signalnest:change-me@localhost:5432/signalnest"
    log_level: str = "INFO"

    # worker
    worker_id: str = ""  # empty -> "<hostname>-<pid>"
    max_concurrency: int = 20
    poll_interval_ms: int = 1000
    claim_batch_max: int = 50
    shutdown_grace_s: int = 20
    heartbeat_interval_s: int = 10

    # observability / housekeeping
    metrics_basic_auth: str = ""  # "user:pass"; empty = internal network only
    metrics_port: int = 9100  # worker metrics listener; do not publish it
    run_retention_days: int = 14
    retention_interval_s: int = 3600

    # SSRF guard (docs/09 section 3). The CIDR override is for dev/test only; empty in prod.
    allowed_ports: str = "80,443,8000-8999"
    ssrf_allow_private_cidrs: str = ""
    allowed_hosts: str = ""

    # auth / sessions
    allow_registration: bool = True
    session_ttl_hours: int = 168
    cookie_secure: bool = False  # set true behind HTTPS (prod)
    expose_docs: bool = True
    static_dir: str = ""  # built SPA; empty disables static serving (dev uses Vite)

    # limits
    max_monitors_per_user: int = 50
    max_monitors_per_host: int = 5
    min_interval_s: int = 30  # tests lower this

    # Seed/CLI convenience only.
    dev_user_email: str = "dev@signalnest.local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
