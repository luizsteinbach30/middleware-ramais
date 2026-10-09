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
- **Módulo não aceito não roda** (CONTRATO §13.8): a verificação ``pausada`` — ou de
  módulo desligado, se o estado ainda não foi aplicado — fica fora da rodada.
- **CFTV** (§13.9): a leitura nova é comparada com a guardada, as diferenças viram
  eventos ``cftv.*`` (com a verificação), e a leitura substitui a anterior. Leitura
  que falhou não apaga a anterior.
"""

from __future__ import annotations

import asyncio
import json
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
    LeituraDeCftv,
    VerificacaoDeEquipamento,
)
from middleware_monitor.domain.config.repository import load_config
from middleware_monitor.domain.equipamentos import cftv, estados, modulos, sondas
from middleware_monitor.domain.equipamentos.lista import credencial_de, parametros_de
from middleware_monitor.integrations.cftv import registry as cftv_registry
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
    assinatura: tuple[str, str, str, str]
    credencial: cftv.Credencial | None = None
    # A credencial existe e não abriu (APP_SECRET_KEY trocada): falha sem ir ao gravador.
    falha_previa: str | None = None


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _impressao_usada(eq: Equipamento, tipo: str, parametros: dict[str, Any]) -> str:
    """A impressão da credencial que a verificação usa ("" quando não usa nenhuma)."""
    if tipo != "cftv":
        return ""
    try:
        tipo_da_credencial = cftv_registry.get_adapter(str(parametros.get("adaptador"))).tipo_de_credencial
    except KeyError:
        return ""
    return next((c.impressao for c in eq.credenciais if c.tipo == tipo_da_credencial), "")


def assinatura(vf: VerificacaoDeEquipamento, eq: Equipamento) -> tuple[str, str, str, str]:
    return (vf.tipo, vf.parametros, eq.host, _impressao_usada(eq, vf.tipo, parametros_de(vf)))


def abrir_credencial(
    eq: Equipamento, parametros: dict[str, Any]
) -> tuple[cftv.Credencial | None, str | None]:
    """A credencial que o adaptador da verificação cftv usa, aberta — ou o motivo."""
    try:
        tipo = cftv_registry.get_adapter(str(parametros.get("adaptador"))).tipo_de_credencial
    except KeyError:
        return None, None
    try:
        aberta = credencial_de(eq, tipo)
    except ValueError:
        return None, f"credencial {tipo} ilegível nesta máquina (a APP_SECRET_KEY mudou?)"
    if aberta is None:
        return None, None
    return cftv.Credencial(usuario=aberta[0], segredo=aberta[1], impressao=aberta[2]), None


def _vencidas(agora: datetime) -> list[_Pendente]:
    saida = []
    with session_factory() as db:
        aceitos = modulos.aceitos(db)
        equipamentos = db.scalars(
            select(Equipamento).options(
                selectinload(Equipamento.verificacoes), selectinload(Equipamento.credenciais)
            )
        ).all()
        for eq in equipamentos:
            for vf in eq.verificacoes:
                if vf.ultima_em is not None and vf.ultima_em + timedelta(seconds=vf.intervalo_s) > agora:
                    continue
                parametros = parametros_de(vf)
                modulo = modulos.modulo_da_verificacao(vf.tipo, parametros)
                if vf.estado == estados.PAUSADA or modulo not in aceitos:
                    continue
                credencial, falha = abrir_credencial(eq, parametros) if vf.tipo == "cftv" else (None, None)
                saida.append(
                    _Pendente(
                        vf.id, eq.id, vf.tipo, eq.host, parametros, assinatura(vf, eq), credencial, falha
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
        if p.falha_previa is not None:
            return p, ResultadoDaSonda(ok=False, erro=p.falha_previa), _agora()
        # Só a cftv leva verificação e credencial: as outras sondas não usam.
        extra: dict[str, Any] = (
            {"verificacao": p.verificacao, "credencial": p.credencial} if p.tipo == "cftv" else {}
        )
        async with sem:
            r = await sondas.executar(
                p.tipo, p.host, p.parametros, timeout_ping_ms=cfg.ping_timeout_ms, **extra
            )
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
                .options(selectinload(Equipamento.verificacoes), selectinload(Equipamento.credenciais))
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
                if vf is None or vf.estado == estados.PAUSADA or assinatura(vf, eq) != p.assinatura:
                    continue  # a lista mudou no meio: o resultado mediu outra coisa
                if vf.tipo == "cftv" and r.dados is not None:
                    eventos += _leitura_de_cftv(db, eq, vf, r.dados, em)
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


def _leitura_de_cftv(
    db: Any, eq: Equipamento, vf: VerificacaoDeEquipamento, novo: dict[str, Any], em: datetime
) -> int:
    """Guarda a leitura nova e registra as transições desde a anterior. Devolve
    quantos eventos nasceram."""
    guardada = db.get(LeituraDeCftv, eq.id)
    anterior = None
    if guardada is not None and guardada.verificacao_id == vf.id:
        try:
            anterior = json.loads(guardada.leitura or "{}")
        except ValueError:
            anterior = None
    transicoes = cftv.eventos(anterior, novo)
    for tipo, de, para, detalhe in transicoes:
        db.add(
            EventoDeEquipamento(
                equipamento_id=eq.id,
                verificacao_id=vf.id,
                tipo=tipo,
                de=de[:16],
                para=para[:16],
                em=em,
                detalhe=detalhe[:500],
            )
        )
        log.info("cftv_transicao", equipamento=eq.id, nome=eq.nome, tipo=tipo, detalhe=detalhe)
    texto = json.dumps(novo, ensure_ascii=False, sort_keys=True)
    if guardada is None:
        db.add(LeituraDeCftv(equipamento_id=eq.id, verificacao_id=vf.id, leitura=texto, lido_em=em))
    else:
        guardada.verificacao_id, guardada.leitura, guardada.lido_em = vf.id, texto, em
    return len(transicoes)
