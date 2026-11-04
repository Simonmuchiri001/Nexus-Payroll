"""Runtime configuration loaded from the environment.

No credential or signing secret has a usable default.  A deployment must provide
``NEXUS_JWT_SECRET`` (and ``NEXUS_DATABASE_URL``) explicitly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated process configuration."""

    model_config = SettingsConfigDict(
        env_prefix="NEXUS_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "development"
    app_name: str = "Nexus Enterprise"
    api_v1_prefix: str = "/api/v1"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

    database_url: str = Field(min_length=1)
    database_echo: bool = False

    jwt_secret: SecretStr
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_issuer: str = "nexus-enterprise"
    access_token_minutes: int = Field(default=15, ge=5, le=120)
    refresh_token_days: int = Field(default=14, ge=1, le=90)

    password_min_length: int = Field(default=12, ge=12, le=128)
    max_login_attempts: int = Field(default=5, ge=3, le=20)
    login_window_seconds: int = Field(default=300, ge=60, le=3600)

    cors_origins: list[str] = Field(default_factory=list)
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])

    @model_validator(mode="after")
    def validate_production_security(self) -> Settings:
        secret = self.jwt_secret.get_secret_value()
        if len(secret) < 32:
            raise ValueError("NEXUS_JWT_SECRET must contain at least 32 characters")
        if self.environment == "production":
            if secret.lower().startswith(("dev-", "test-", "change-me")):
                raise ValueError("Production JWT secret uses a forbidden development value")
            if not self.database_url.startswith(("postgresql+asyncpg://", "postgresql://")):
                raise ValueError("Production requires PostgreSQL")
        return self

    @property
    def is_testing(self) -> bool:
        return self.environment == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the validated, process-wide settings singleton."""

    return Settings()  # pyright: ignore[reportCallIssue]  # values are supplied by the environment
