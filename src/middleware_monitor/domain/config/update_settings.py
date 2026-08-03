"""Configuração do auto-update, persistida em ``app_config`` (v2.7.0).

Até a v2.6.0 a tela `/system/updates` tinha um seletor de canal e um toggle de
auto-update **decorativos**: sem handler, sem endpoint de escrita, e o
``auto_update`` exibido vinha de um literal ``True`` no código. Canal e repo só
mudavam editando o `.env` e reiniciando.

Decisão de produto (2026-08-03): o agendamento **só verifica e avisa** — nunca
instala sozinho. Instalar continua sendo o clique em "Atualizar agora". O que
o operador configura aqui é *quando* verificar e em *qual canal*.

As chaves vivem no mesmo KV das demais configs (`app_config`), com o `.env`
servindo de fallback para o canal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.models import AppConfig
from middleware_monitor.settings import get_settings

# Prefixo próprio para não colidir com as chaves da tela /config.
_PREFIX = "update."
KEY_AUTO_CHECK = f"{_PREFIX}auto_check"
KEY_CHANNEL = f"{_PREFIX}channel"
KEY_HOUR = f"{_PREFIX}check_hour"
KEY_MINUTE = f"{_PREFIX}check_minute"
KEY_DAYS = f"{_PREFIX}check_days"

CHANNELS: tuple[str, ...] = ("stable", "beta")
# APScheduler aceita `day_of_week` como "mon,tue,..."; guardamos nesse formato.
WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAYS_LABEL: dict[str, str] = {
    "mon": "seg", "tue": "ter", "wed": "qua", "thu": "qui",
    "fri": "sex", "sat": "sáb", "sun": "dom",
}

_TRUE = {"1", "true", "True", "on", "yes"}


@dataclass(frozen=True)
class UpdateSettings:
    """Configuração efetiva do auto-update."""

    auto_check: bool = True
    channel: str = "stable"
    check_hour: int = 0
    check_minute: int = 0
    # Dias em que a verificação roda. Vazio nunca acontece: cai no padrão.
    check_days: tuple[str, ...] = field(default=WEEKDAYS)

    @property
    def day_of_week(self) -> str:
        """Formato aceito pelo `CronTrigger` do APScheduler."""
        return ",".join(self.check_days)

    def as_dict(self) -> dict[str, object]:
        return {
            "auto_check": self.auto_check,
            "channel": self.channel,
            "check_hour": self.check_hour,
            "check_minute": self.check_minute,
            "check_days": list(self.check_days),
        }


def _rows(db: DBSession) -> dict[str, str]:
    return {
        row.key: row.value
        for row in db.scalars(
            select(AppConfig).where(AppConfig.key.startswith(_PREFIX))
        ).all()
    }


def _as_int(raw: str | None, default: int, *, low: int, high: int) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


def normalize_days(raw: object) -> tuple[str, ...]:
    """Aceita lista ou string 'mon,tue' e devolve os dias válidos, em ordem.

    Sem nenhum dia válido a verificação nunca rodaria — o que é indistinguível
    de "desligado" e confunde. Nesse caso volta a semana inteira; para pausar,
    existe o `auto_check`.
    """
    if isinstance(raw, str):
        itens = [p.strip().lower() for p in raw.split(",")]
    elif isinstance(raw, (list, tuple)):
        itens = [str(p).strip().lower() for p in raw]
    else:
        return WEEKDAYS
    validos = tuple(d for d in WEEKDAYS if d in itens)
    return validos or WEEKDAYS


def load_update_settings(db: DBSession) -> UpdateSettings:
    """Lê a config do KV; o canal cai no `.env` quando nunca foi definido."""
    rows = _rows(db)
    canal_raw = rows.get(KEY_CHANNEL) or get_settings().update_channel
    canal = canal_raw if canal_raw in CHANNELS else "stable"
    auto_raw = rows.get(KEY_AUTO_CHECK)
    return UpdateSettings(
        auto_check=True if auto_raw is None else auto_raw in _TRUE,
        channel=canal,
        check_hour=_as_int(rows.get(KEY_HOUR), 0, low=0, high=23),
        check_minute=_as_int(rows.get(KEY_MINUTE), 0, low=0, high=59),
        check_days=normalize_days(rows.get(KEY_DAYS)),
    )


def save_update_settings(
    db: DBSession, incoming: dict[str, object], *, user_id: int | None = None,
) -> UpdateSettings:
    """Aplica um update parcial (só as chaves presentes) e devolve o resultado."""
    from datetime import UTC, datetime

    existentes = {
        row.key: row
        for row in db.scalars(
            select(AppConfig).where(AppConfig.key.startswith(_PREFIX))
        ).all()
    }
    agora = datetime.now(UTC).replace(tzinfo=None)

    def grava(key: str, value: str) -> None:
        row = existentes.get(key)
        if row is None:
            db.add(AppConfig(
                key=key, value=value, is_secret=False,
                updated_at=agora, updated_by=user_id,
            ))
        else:
            row.value = value
            row.updated_at = agora
            row.updated_by = user_id

    if "auto_check" in incoming:
        grava(KEY_AUTO_CHECK, "1" if incoming["auto_check"] else "0")
    if "channel" in incoming:
        canal = str(incoming["channel"])
        if canal not in CHANNELS:
            raise ValueError(f"canal inválido: {canal!r} (use {' ou '.join(CHANNELS)})")
        grava(KEY_CHANNEL, canal)
    if "check_hour" in incoming:
        hora = _as_int(str(incoming["check_hour"]), -1, low=0, high=23)
        if hora < 0:
            raise ValueError("hora inválida (0-23)")
        grava(KEY_HOUR, str(hora))
    if "check_minute" in incoming:
        minuto = _as_int(str(incoming["check_minute"]), -1, low=0, high=59)
        if minuto < 0:
            raise ValueError("minuto inválido (0-59)")
        grava(KEY_MINUTE, str(minuto))
    if "check_days" in incoming:
        grava(KEY_DAYS, ",".join(normalize_days(incoming["check_days"])))

    db.commit()
    return load_update_settings(db)
