"""Casamento ramal → device, ambiente e linha do Configurador.

O painel ao vivo mostra o estado que veio do broker, mas a pergunta seguinte é
sempre "e o que eu faço a respeito": abrir o telefone, ver o ambiente, conferir
se a última aplicação de configuração falhou. Este módulo resolve esses vínculos
em **uma** consulta e os entrega prontos para o cartão.

**Por que tem cache.** O painel recarrega a cada 2,5 s e a instalação real tem
~800 ramais publicando; refazer três junções a cada ciclo custaria mais que a
própria ingestão. O que este índice devolve muda em escala de minutos (device
criado pela coleta, linha revinculada, aplicação executada), então um TTL curto
não atrasa nada perceptível — e o estado de verdade, o do ramal, continua vindo
da memória do coletor a cada ciclo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from middleware_monitor.core.models import (
    Device,
    ExtensionEnvironment,
    ExtensionLine,
    UscallServer,
)

if TYPE_CHECKING:  # pragma: no cover - só para tipagem
    from collections.abc import Iterable

    from sqlalchemy.orm import Session as DBSession

TTL_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class RamalLinks:
    """O que existe no resto do sistema sobre um ramal visto no MQTT."""

    device_id: int | None = None
    ip: str | None = None
    network_status: str = "unknown"
    mac: str | None = None
    model: str | None = None
    uscall_server: str | None = None
    environment_id: str | None = None
    environment_nome: str | None = None
    line_status: str | None = None
    line_error: str | None = None


VAZIO = RamalLinks()

_cache: dict[str, RamalLinks] = {}
_cache_ramais: frozenset[str] = frozenset()
_cache_at: float = 0.0


def invalidate() -> None:
    """Descarta o índice. Chamado nos testes e quando o vínculo muda na mão."""
    global _cache, _cache_ramais, _cache_at
    _cache = {}
    _cache_ramais = frozenset()
    _cache_at = 0.0


def _consultar(db: DBSession, ramais: frozenset[str]) -> dict[str, RamalLinks]:
    if not ramais:
        return {}
    # Uma linha por device. Os LEFT JOIN garantem que device sem servidor, sem
    # linha ou sem ambiente continue aparecendo — o cartão precisa do IP mesmo
    # quando o telefone não está em ambiente nenhum.
    stmt = (
        select(
            Device.name,
            Device.id,
            Device.ip,
            Device.network_status,
            Device.mac,
            Device.model,
            UscallServer.nome,
            ExtensionLine.environment_id,
            ExtensionEnvironment.nome,
            ExtensionLine.ultimo_status,
            ExtensionLine.ultimo_erro,
        )
        .select_from(Device)
        .outerjoin(UscallServer, Device.uscall_server_id == UscallServer.id)
        .outerjoin(ExtensionLine, ExtensionLine.device_id == Device.id)
        .outerjoin(
            ExtensionEnvironment,
            ExtensionLine.environment_id == ExtensionEnvironment.id,
        )
        .where(Device.name.in_(ramais))
        .order_by(Device.name, ExtensionLine.updated_at.desc())
    )
    out: dict[str, RamalLinks] = {}
    for row in db.execute(stmt).all():
        nome = str(row[0])
        # Nada impede duas linhas apontarem para o mesmo device (o vínculo é por
        # IP e o operador pode repetir). Fica a mais recente, que é a ordenação
        # acima — e é a que o operador acabou de mexer.
        if nome in out:
            continue
        out[nome] = RamalLinks(
            device_id=row[1],
            ip=row[2],
            network_status=row[3] or "unknown",
            mac=row[4],
            model=row[5],
            uscall_server=row[6],
            environment_id=row[7],
            environment_nome=row[8],
            line_status=row[9],
            line_error=row[10],
        )
    return out


def index(db: DBSession, ramais: Iterable[str]) -> dict[str, RamalLinks]:
    """Vínculos dos ramais pedidos, servindo do cache quando ele ainda vale."""
    global _cache, _cache_ramais, _cache_at
    pedidos = frozenset(str(r) for r in ramais)
    agora = time.monotonic()
    valido = (agora - _cache_at) < TTL_SECONDS and pedidos <= _cache_ramais
    if not valido:
        _cache = _consultar(db, pedidos)
        _cache_ramais = pedidos
        _cache_at = agora
    return {r: _cache.get(r, VAZIO) for r in pedidos}
