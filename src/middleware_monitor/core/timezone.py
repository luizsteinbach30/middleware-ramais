"""Descoberta do fuso horário do servidor.

Existe porque o middleware precisa dizer aos telefones **em que fuso eles estão**,
e a resposta certa quase sempre é "o mesmo do servidor que os administra" — sem
ninguém digitar nada.

Duas representações, porque os firmwares se dividem: o HTEK e o Intelbras S3002
querem um **id de tabela** (que só se obtém a partir do nome do fuso), enquanto o
Yealink e o Intelbras V-series querem o **offset numérico**. Guardar as duas
evita que cada adapter tente reconstruir a que falta.

Nada aqui pode levantar: isto roda no caminho que calcula o status de toda a
planilha de ramais, e um erro de fuso não pode virar HTTP 500 numa tela que não
tem nada a ver com hora.
"""

from __future__ import annotations

import os
import zoneinfo
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from middleware_monitor.core.logging import get_logger

__all__ = [
    "FALLBACK_TZ",
    "ServerTimezone",
    "detect_server_timezone",
    "forget_detection",
    "is_valid_iana",
    "offset_minutes_for",
]

log = get_logger("core.timezone")

# Último recurso quando o SO não coopera. É o fuso de praticamente toda
# instalação deste produto; melhor um palpite explícito e registrado no log do
# que deixar o telefone com a hora de fábrica.
FALLBACK_TZ = "America/Sao_Paulo"
FALLBACK_OFFSET = -180


@dataclass(frozen=True, slots=True)
class ServerTimezone:
    """O que se conseguiu descobrir sobre o fuso desta máquina.

    ``name`` é ``None`` quando o SO só entregou o offset — acontece em máquina
    com fuso customizado. O offset **sempre** vem preenchido, porque
    ``datetime.astimezone()`` funciona em qualquer sistema.
    """

    name: str | None
    offset_minutes: int
    source: str  # tzlocal | env_tz | offset_only | fallback

    @property
    def label(self) -> str:
        """``'America/Sao_Paulo (UTC-03:00)'`` — o que a tela mostra."""
        sinal = "-" if self.offset_minutes < 0 else "+"
        h, m = divmod(abs(self.offset_minutes), 60)
        utc = f"UTC{sinal}{h:02d}:{m:02d}"
        return f"{self.name} ({utc})" if self.name else utc


def is_valid_iana(name: str) -> bool:
    """O nome existe na base IANA desta instalação?

    Serve para descartar o que o Windows devolve por ``astimezone()`` — lá o
    ``tzinfo`` vem com o nome localizado ("Hora oficial do Brasil"), que não é
    chave de fuso e não serve para nada além de exibir.
    """
    if not name or "/" not in name:
        return False
    try:
        return name in zoneinfo.available_timezones()
    except Exception:  # pragma: no cover - base IANA ausente
        return False


def offset_minutes_for(name: str, *, when: datetime | None = None) -> int | None:
    """Offset do fuso **no instante dado** (o de agora, por padrão).

    Depende do instante de propósito: em fuso com horário de verão o valor muda
    ao longo do ano, e o telefone precisa do que vale hoje. No Brasil isso é
    inócuo desde 2019, quando o horário de verão acabou.
    """
    if not is_valid_iana(name):
        return None
    try:
        momento = when or datetime.now(UTC)
        deslocamento = zoneinfo.ZoneInfo(name).utcoffset(momento.replace(tzinfo=None))
    except Exception:
        return None
    return None if deslocamento is None else int(deslocamento.total_seconds() // 60)


def _os_offset_minutes() -> int:
    try:
        deslocamento = datetime.now().astimezone().utcoffset()
    except Exception:  # pragma: no cover - SO sem fuso configurado
        return FALLBACK_OFFSET
    return FALLBACK_OFFSET if deslocamento is None else int(deslocamento.total_seconds() // 60)


@lru_cache(maxsize=1)
def detect_server_timezone() -> ServerTimezone:
    """Fuso desta máquina, em cache — o fuso do SO não muda com o serviço no ar.

    Ordem: ``tzlocal`` (resolve o nome IANA nos dois sistemas — no Windows pela
    tabela CLDR embutida, no Linux por ``/etc/localtime``), depois a variável
    ``TZ``, e por fim só o offset. Use ``forget_detection()`` para reler.
    """
    offset = _os_offset_minutes()

    candidatos: list[tuple[str, str]] = []
    try:
        import tzlocal

        nome = tzlocal.get_localzone_name()
        if nome:
            candidatos.append((str(nome), "tzlocal"))
    except Exception as exc:
        log.warning("tzlocal_indisponivel", error=type(exc).__name__, message=str(exc))

    if tz_env := os.environ.get("TZ", "").strip():
        candidatos.append((tz_env, "env_tz"))

    for nome, fonte in candidatos:
        if not is_valid_iana(nome):
            continue
        do_nome = offset_minutes_for(nome)
        if do_nome is not None and do_nome != offset:
            # O nome pode estar desatualizado em relação ao que o SO aplica.
            # O offset é o que o relógio da máquina realmente usa, então ele
            # manda — mas o desacordo fica registrado, porque costuma ser
            # sintoma de base de fusos velha.
            log.warning(
                "timezone_nome_diverge_do_offset",
                nome=nome, offset_do_nome=do_nome, offset_do_so=offset,
            )
        return ServerTimezone(name=nome, offset_minutes=offset, source=fonte)

    log.warning("timezone_sem_nome_iana", offset_minutes=offset)
    return ServerTimezone(name=None, offset_minutes=offset, source="offset_only")


def forget_detection() -> None:
    """Descarta o cache — usado pelo botão "redetectar" e pelos testes.

    Tolera a função ter sido substituída (teste que fixa um fuso), porque quem
    chama é código de produção que não pode quebrar por causa disso.
    """
    limpar = getattr(detect_server_timezone, "cache_clear", None)
    if limpar is not None:
        limpar()
