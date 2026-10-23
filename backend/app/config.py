from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://signalnest:change-me@localhost:5432/signalnest"
    log_level: str = "INFO"

    # worker (runs inside the API process)
    run_worker: bool = True
    batch_size: int = 20  # most checks running at the same time
    poll_interval_ms: int = 1000
    retention_days: int = 30  # how long check history is kept
    shutdown_grace_s: int = 20  # how long to let running checks finish when stopping

    # SSRF guard. The CIDR override is for dev/test only; keep it empty otherwise.
    ssrf_allow_private_cidrs: str = ""
    allowed_ports: str = "80,443,8000-8999"  # ports a monitor or webhook may use

    # auth / sessions
    allow_registration: bool = True
    session_ttl_hours: int = 168
    cookie_secure: bool = False  # set true behind HTTPS
    expose_docs: bool = True
    static_dir: str = ""  # built SPA; empty disables static serving (dev uses Vite)

    # login throttle: this many failed logins for one email within the window locks it
    login_max_failures: int = 5
    login_window_s: int = 900
    # extra browser origins allowed to make state-changing requests (comma separated);
    # the app's own origin is always allowed
    trusted_origins: str = ""

    # limits
    max_monitors_per_user: int = 50
    min_interval_s: int = 30  # tests lower this

    # Seed/CLI convenience only.
    dev_user_email: str = "dev@signalnest.local"

    @field_validator("database_url")
    @classmethod
    def _asyncpg_driver(cls, v: str) -> str:
        # Hosting providers hand out postgres:// or postgresql:// URLs; we need the async driver.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+asyncpg://" + v[len(prefix) :]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
