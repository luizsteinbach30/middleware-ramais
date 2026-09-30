"""Job: a disponibilidade dos equipamentos (AGENTE-NOC item 19, CONTRATO §13.4).

No molde do ``monitor_devices``: semáforo de concorrência, tempo limite em cada
sonda, resultado gravado por verificação. A diferença é o relógio: cada
verificação tem o próprio intervalo (30 s no mínimo), então o job acorda a cada
``TICK_S`` e roda só as que venceram.

- **Uma amostra por execução** (``equipamento_amostras``) — anda para o NOC por cursor.
- **Evento só na transição do equipamento** (``equipamento_eventos``), e a queda
  só depois de ``tentativasParaCair`` falhas seguidas (``domain/equipamentos/estados.py``).
- **Só leitura.** O job de telefonia (e o reaplicar automático que mora nele) não
  é tocado, e nenhuma sonda escreve no equipamento.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import (
    AmostraDeVerificacao,
    Equipamento,
    EventoDeEquipamento,
    VerificacaoDeEquipamento,
)
from middleware_monitor.domain.config.repository import load_config
from middleware_monitor.domain.equipamentos import estados, sondas
from middleware_monitor.domain.equipamentos.lista import parametros_de
from middleware_monitor.integrations.network.base import ResultadoDaSonda

log = get_logger("monitor_equipamentos")

JOB_ID = "monitor_equipamentos"
TICK_S = 10


@dataclass(frozen=True)
class _Pendente:
    verificacao: str
    equipamento: str
    tipo: str
    host: str
    parametros: dict[str, Any]
    # O que foi medido: se a lista mudou durante a rodada, o resultado não vale.
    assinatura: tuple[str, str, str]


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _vencidas(agora: datetime) -> list[_Pendente]:
    with session_factory() as db:
        linhas = db.execute(
            select(VerificacaoDeEquipamento, Equipamento.host).join(
                Equipamento, Equipamento.id == VerificacaoDeEquipamento.equipamento_id
            )
        ).all()
    saida = []
    for vf, host in linhas:
        if vf.ultima_em is not None and vf.ultima_em + timedelta(seconds=vf.intervalo_s) > agora:
            continue
        saida.append(
            _Pendente(
                vf.id, vf.equipamento_id, vf.tipo, host, parametros_de(vf), (vf.tipo, vf.parametros, host)
            )
        )
    return saida


async def run_monitor_equipamentos() -> int:
    """Uma rodada. Devolve quantas verificações rodaram."""
    inicio = time.perf_counter()
    pendentes = _vencidas(_agora())
    if not pendentes:
        return 0
    with session_factory() as db:
        cfg = load_config(db)
    sem = asyncio.Semaphore(max(1, min(cfg.ping_concurrency, 200)))

    async def uma(p: _Pendente) -> tuple[_Pendente, ResultadoDaSonda, datetime]:
        async with sem:
            r = await sondas.executar(p.tipo, p.host, p.parametros, timeout_ping_ms=cfg.ping_timeout_ms)
        return p, r, _agora()

    resultados = await asyncio.gather(*(uma(p) for p in pendentes))
    eventos = aplicar_resultados(resultados)
    log.info(
        "monitor_equipamentos_ok",
        verificacoes=len(resultados),
        falhas=sum(1 for _p, r, _em in resultados if not r.ok),
        eventos=eventos,
        duration_ms=int((time.perf_counter() - inicio) * 1000),
    )
    return len(resultados)


def aplicar_resultados(resultados: list[tuple[_Pendente, ResultadoDaSonda, datetime]]) -> int:
    """Grava amostras, avança a máquina de estados e registra as transições. Devolve
    quantos eventos nasceram."""
    por_equipamento: dict[str, list[tuple[_Pendente, ResultadoDaSonda, datetime]]] = {}
    for item in resultados:
        por_equipamento.setdefault(item[0].equipamento, []).append(item)
    eventos = 0
    with session_factory() as db:
        equipamentos = {
            eq.id: eq
            for eq in db.scalars(
                select(Equipamento)
                .where(Equipamento.id.in_(list(por_equipamento)))
                .options(selectinload(Equipamento.verificacoes))
            ).all()
        }
        for eid, itens in por_equipamento.items():
            eq = equipamentos.get(eid)
            if eq is None:
                continue  # removido durante a rodada
            vfs = {vf.id: vf for vf in eq.verificacoes}
            mudaram: list[VerificacaoDeEquipamento] = []
            ultima = None
            for p, r, em in sorted(itens, key=lambda x: x[2]):
                vf = vfs.get(p.verificacao)
                if vf is None or (vf.tipo, vf.parametros, eq.host) != p.assinatura:
                    continue  # a lista mudou no meio: o resultado mediu outra coisa
                db.add(
                    AmostraDeVerificacao(
                        verificacao_id=vf.id, em=em, ok=r.ok, valor=r.valor if r.ok else None
                    )
                )
                if estados.registrar(
                    vf,
                    ok=r.ok,
                    valor=r.valor,
                    erro=r.erro,
                    em=em,
                    tentativas_para_cair=eq.tentativas_para_cair,
                ):
                    mudaram.append(vf)
                ultima = em
            if ultima is None:
                continue
            novo = estados.estado_do_equipamento(eq.verificacoes)
            if novo == eq.estado:
                continue
            tipo = estados.tipo_do_evento(eq.estado, novo)
            if tipo is not None:
                causa = estados.causa(tipo, mudaram)
                db.add(
                    EventoDeEquipamento(
                        equipamento_id=eq.id,
                        verificacao_id=causa.id if causa else None,
                        tipo=tipo,
                        de=eq.estado,
                        para=novo,
                        em=ultima,
                        detalhe=estados.detalhe(tipo, causa, eq.tentativas_para_cair),
                    )
                )
                eventos += 1
                log.info("equipamento_transicao", equipamento=eq.id, nome=eq.nome, de=eq.estado, para=novo)
            eq.estado, eq.estado_desde = novo, ultima
        db.commit()
    return eventos
