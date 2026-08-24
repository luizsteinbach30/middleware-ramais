"""Configuracao do backup automatico, persistida em ``app_config``.

Mesmo padrao do auto-update (``domain/config/update_settings.py``): chaves com
prefixo proprio no KV, dataclass congelada na leitura, update parcial na
escrita.

Decisao de desenho: o job automatico grava **sempre** o snapshot do banco (nao
precisa de segredo nenhum, e o arquivo fica na propria maquina) e grava o
pacote portavel **so** quando existe uma passphrase salva. Sem passphrase nao
ha como cifrar o pacote, e gravar configuracao com token/senha em claro no
disco seria pior que nao ter backup nenhum.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.crypto import SecretBox
from middleware_monitor.core.models import AppConfig
from middleware_monitor.settings import get_settings

_PREFIX = "backup."
KEY_AUTO = f"{_PREFIX}auto_enabled"
KEY_HOUR = f"{_PREFIX}hour"
KEY_MINUTE = f"{_PREFIX}minute"
KEY_KEEP = f"{_PREFIX}keep"
KEY_MAX_MB = f"{_PREFIX}max_mb"
KEY_PASSPHRASE = f"{_PREFIX}export_passphrase"
KEY_LAST_AT = f"{_PREFIX}last_run_at"
KEY_LAST_STATUS = f"{_PREFIX}last_status"
KEY_LAST_DETAIL = f"{_PREFIX}last_detail"

# Chaves que NAO viajam no pacote portavel: passphrase e o proprio estado da
# ultima execucao sao locais desta instalacao.
LOCAL_ONLY_KEYS: frozenset[str] = frozenset(
    {KEY_PASSPHRASE, KEY_LAST_AT, KEY_LAST_STATUS, KEY_LAST_DETAIL}
)

_TRUE = {"1", "true", "True", "on", "yes"}


@dataclass(frozen=True)
class BackupSettings:
    """Configuracao efetiva do backup automatico."""

    auto_enabled: bool = True
    hour: int = 2
    minute: int = 30
    # Quantas copias manter. A poda respeita ESTE limite e o de espaco: vale o
    # que cortar primeiro, porque o banco com ledger MQTT cresce rapido e sete
    # copias de um banco grande enchem o disco sem aviso.
    keep: int = 7
    max_mb: int = 2048
    has_passphrase: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "auto_enabled": self.auto_enabled,
            "hour": self.hour,
            "minute": self.minute,
            "keep": self.keep,
            "max_mb": self.max_mb,
            "has_passphrase": self.has_passphrase,
        }


def _box() -> SecretBox:
    return SecretBox(get_settings().secret_key)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _rows(db: DBSession) -> dict[str, AppConfig]:
    return {
        row.key: row
        for row in db.scalars(
            select(AppConfig).where(AppConfig.key.startswith(_PREFIX))
        ).all()
    }


def _as_int(raw: object, default: int, *, low: int, high: int) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


def load_backup_settings(db: DBSession) -> BackupSettings:
    rows = _rows(db)
    auto_raw = rows[KEY_AUTO].value if KEY_AUTO in rows else None
    pass_row = rows.get(KEY_PASSPHRASE)
    return BackupSettings(
        auto_enabled=True if auto_raw is None else auto_raw in _TRUE,
        hour=_as_int(rows[KEY_HOUR].value if KEY_HOUR in rows else None, 2, low=0, high=23),
        minute=_as_int(rows[KEY_MINUTE].value if KEY_MINUTE in rows else None, 30, low=0, high=59),
        keep=_as_int(rows[KEY_KEEP].value if KEY_KEEP in rows else None, 7, low=1, high=365),
        max_mb=_as_int(rows[KEY_MAX_MB].value if KEY_MAX_MB in rows else None, 2048, low=0, high=1_000_000),
        has_passphrase=bool(pass_row and pass_row.value),
    )


def save_backup_settings(
    db: DBSession, incoming: dict[str, object], *, user_id: int | None = None,
) -> BackupSettings:
    """Update parcial. ``export_passphrase`` ausente mantem a atual; string
    vazia apaga; string nao-vazia cifra e grava."""
    rows = _rows(db)
    agora = _now()

    def grava(key: str, value: str, *, secret: bool = False) -> None:
        row = rows.get(key)
        if row is None:
            row = AppConfig(
                key=key, value=value, is_secret=secret,
                updated_at=agora, updated_by=user_id,
            )
            db.add(row)
            rows[key] = row
        else:
            row.value = value
            row.is_secret = secret
            row.updated_at = agora
            row.updated_by = user_id

    if "auto_enabled" in incoming:
        grava(KEY_AUTO, "1" if incoming["auto_enabled"] else "0")
    if "hour" in incoming:
        hora = _as_int(incoming["hour"], -1, low=0, high=23)
        if hora < 0:
            raise ValueError("hora invalida (0-23)")
        grava(KEY_HOUR, str(hora))
    if "minute" in incoming:
        minuto = _as_int(incoming["minute"], -1, low=0, high=59)
        if minuto < 0:
            raise ValueError("minuto invalido (0-59)")
        grava(KEY_MINUTE, str(minuto))
    if "keep" in incoming:
        keep = _as_int(incoming["keep"], -1, low=1, high=365)
        if keep < 0:
            raise ValueError("quantidade de copias invalida (1-365)")
        grava(KEY_KEEP, str(keep))
    if "max_mb" in incoming:
        teto = _as_int(incoming["max_mb"], -1, low=0, high=1_000_000)
        if teto < 0:
            raise ValueError("limite de espaco invalido")
        grava(KEY_MAX_MB, str(teto))
    if "export_passphrase" in incoming:
        frase = str(incoming["export_passphrase"] or "")
        grava(KEY_PASSPHRASE, _box().encrypt(frase) if frase else "", secret=bool(frase))

    db.commit()
    return load_backup_settings(db)


def load_export_passphrase(db: DBSession) -> str:
    """Passphrase salva para o pacote portavel automatico ("" se nao houver)."""
    row = db.get(AppConfig, KEY_PASSPHRASE)
    if row is None or not row.value:
        return ""
    try:
        return _box().decrypt(row.value)
    except ValueError:
        # Chave da instalacao trocada: a passphrase antiga virou lixo. Melhor
        # perder o pacote portavel do que abortar o backup do banco.
        return ""


def record_run(
    db: DBSession, *, status: str, detail: str = "", user_id: int | None = None,
) -> None:
    """Grava o resultado da ultima execucao (a tela mostra isso)."""
    save_state = {
        KEY_LAST_AT: _now().isoformat(timespec="seconds"),
        KEY_LAST_STATUS: status,
        KEY_LAST_DETAIL: detail[:500],
    }
    rows = _rows(db)
    for key, value in save_state.items():
        row = rows.get(key)
        if row is None:
            db.add(AppConfig(
                key=key, value=value, is_secret=False,
                updated_at=_now(), updated_by=user_id,
            ))
        else:
            row.value = value
            row.updated_at = _now()
            row.updated_by = user_id
    db.commit()


def load_last_run(db: DBSession) -> dict[str, str]:
    rows = _rows(db)
    return {
        "at": rows[KEY_LAST_AT].value if KEY_LAST_AT in rows else "",
        "status": rows[KEY_LAST_STATUS].value if KEY_LAST_STATUS in rows else "",
        "detail": rows[KEY_LAST_DETAIL].value if KEY_LAST_DETAIL in rows else "",
    }
