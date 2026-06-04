"""Application settings loaded from environment variables / .env file.

Domain-level configuration (intervals, webhooks, USCall) is stored in the
database (table ``app_config``). Only infrastructure-level knobs (paths, ports,
secret material) live here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        case_sensitive=False,
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8080

    data_dir: Path = Field(default=Path("./data"))
    db_url: str = ""

    secret_key: str = "change-me"
    cookie_secure: bool = False
    # Quando True, aborta o boot se secret_key continuar no default 'change-me'.
    # Default False para não quebrar deploys existentes (só loga WARNING).
    require_secret_key: bool = False

    log_level: str = "INFO"
    log_json: bool = False

    update_repo: str = "org/middleware-monitor"
    update_channel: str = "stable"
    update_check_minutes: int = 60
    update_public_key_path: Path | None = None

    metrics_enabled: bool = False

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand_data_dir(cls, value: object) -> Path:
        return Path(str(value)).expanduser().resolve()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def db_dir(self) -> Path:
        return self.data_dir / "db"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_db_url(self) -> str:
        if self.db_url:
            return self.db_url
        return f"sqlite:///{(self.db_dir / 'app.db').as_posix()}"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.db_dir, self.backups_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
