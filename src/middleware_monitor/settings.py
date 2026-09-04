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

# Arquivo de ambiente que o instalador Linux (.run) cria. Existir = instalação
# com systemd, onde quem atualiza é a unidade middleware-monitor-update.
LINUX_INSTALL_MARKER = Path("/etc/middleware-monitor/env")


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

    update_repo: str = "luizsteinbach30/middleware-ramais"
    update_channel: str = "stable"
    update_check_minutes: int = 60
    update_public_key_path: Path | None = None
    # Token de leitura das releases (o repo é privado). Precedência:
    # APP_UPDATE_TOKEN no .env > token embutido no build (_embedded.py).
    update_token: str = ""
    # Como "Atualizar agora" aplica a atualização fora do .exe:
    #   auto    = systemd se existir o env da instalação Linux pelo .run
    #             (LINUX_INSTALL_MARKER); senão o caminho legado
    #   systemd = o serviço só grava APP_DATA_DIR/update.request; a unidade
    #             middleware-monitor-update (root) baixa e instala a release
    #   legacy  = o próprio processo baixa o tarball e troca o symlink
    update_mode: str = "auto"
    # true = o timer diário do Linux instala releases novas sozinho. Lido pelo
    # script middleware-monitor-update, não pela aplicação; está aqui só para
    # o .env documentado não ser rejeitado.
    update_auto_install: bool = False

    metrics_enabled: bool = False

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand_data_dir(cls, value: object) -> Path:
        return Path(str(value)).expanduser().resolve()

    def resolved_update_mode(self) -> str:
        """``auto`` decide pela presença da instalação Linux pelo ``.run``."""
        mode = (self.update_mode or "auto").strip().lower()
        if mode != "auto":
            return mode
        return "systemd" if LINUX_INSTALL_MARKER.exists() else "legacy"

    # Propriedade simples (NÃO computed_field): o token nunca deve aparecer
    # em model_dump()/serialização.
    @property
    def effective_update_token(self) -> str | None:
        if self.update_token:
            return self.update_token
        from middleware_monitor._embedded import embedded_update_token

        return embedded_update_token() or None

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
