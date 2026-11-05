"""Settings, read from the environment or ``.env``."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://crm:crm@127.0.0.1:5440/dpi_crm"
    # The MCP server owns the LinkedIn browser; the backend is one more client.
    mcp_url: str = "http://127.0.0.1:8001/mcp"
    # Bearer token, when the MCP sits behind dpi-lab's Caddy.
    mcp_token: str | None = None
    mcp_timeout_seconds: float = 900.0
    # Cron for the network sync (minute hour day month weekday).
    sync_cron: str = "0 8 * * 1-5"
    scheduler_enabled: bool = True
    # A profile already read is read again only after this many days.
    profile_refresh_days: int = 30
    # Pause between profile visits, in seconds (min, max).
    profile_pause_min: float = 4.0
    profile_pause_max: float = 9.0
    cors_origins: str = "http://localhost:3100"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
