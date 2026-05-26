import os
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that are absolutely unsafe to ship to production. Startup refuses if
# ENV != "dev" and any of these are still set.
_INSECURE_DEFAULTS = {
    "jwt_secret": {"dev-secret-change-me", "change-me", "change-me-in-prod"},
    "admin_password": {"admin", "change-me", "change-me-on-first-login"},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "dev" = local docker-compose / smoke testing. Anything else = enforce
    # production hygiene (real secrets, non-default admin password, TLS host).
    env: str = Field(default="dev", alias="PANEL_ENV")

    database_url: str = Field(
        default="postgresql+asyncpg://panel:panel@localhost:5432/panel",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    jwt_secret: str = Field(default="dev-secret-change-me", alias="JWT_SECRET")
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 60 * 24

    admin_email: str = Field(default="admin@example.com", alias="ADMIN_EMAIL")
    admin_password: str = Field(default="admin", alias="ADMIN_PASSWORD")

    panel_host: str = Field(default="panel.example.com", alias="PANEL_HOST")
    panel_xray_api_addr: str = Field(
        default="127.0.0.1:10085", alias="PANEL_XRAY_API_ADDR"
    )
    panel_xray_config_path: str = Field(
        default="/usr/local/etc/xray/config.json", alias="PANEL_XRAY_CONFIG_PATH"
    )
    panel_reality_private_key: str = Field(
        default="", alias="PANEL_REALITY_PRIVATE_KEY"
    )
    panel_reality_public_key: str = Field(default="", alias="PANEL_REALITY_PUBLIC_KEY")

    panel_reality_dest: str = "www.microsoft.com:443"
    panel_reality_server_name: str = "www.microsoft.com"

    panel_inbound_port_start: int = 10001
    panel_inbound_port_end: int = 10999

    @model_validator(mode="after")
    def _enforce_production_hygiene(self) -> "Settings":
        if self.env == "dev":
            return self
        problems: list[str] = []
        if self.jwt_secret in _INSECURE_DEFAULTS["jwt_secret"] or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET is missing, default, or shorter than 32 chars")
        if self.admin_password in _INSECURE_DEFAULTS["admin_password"]:
            problems.append("ADMIN_PASSWORD is still a default value")
        if not self.panel_reality_private_key or not self.panel_reality_public_key:
            problems.append(
                "PANEL_REALITY_PRIVATE_KEY / PUBLIC_KEY missing "
                "(run scripts/install_panel.sh or `xray x25519`)"
            )
        elif self.panel_reality_private_key.startswith("DEV_"):
            problems.append("PANEL_REALITY_PRIVATE_KEY is a DEV placeholder")
        if self.panel_host in ("panel.example.com", "localhost", "127.0.0.1"):
            problems.append("PANEL_HOST must point to a real domain in production")
        if problems:
            msg = "Refusing to start with insecure config (PANEL_ENV={}):\n  - {}".format(
                self.env, "\n  - ".join(problems)
            )
            raise RuntimeError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
