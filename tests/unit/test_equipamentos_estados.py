"""A máquina de estados e o job ``monitor_equipamentos`` (AGENTE-NOC item 19, CONTRATO §13.4).

As sondas são trocadas por um roteiro (o que cada verificação responde em cada
rodada), e o relógio do job anda 61 s por rodada — o intervalo padrão é 60 s.
O que se prova: 3 falhas seguidas para cair, 1 resposta para voltar, falha
isolada sem evento, ``degradado`` quando o ping responde e outra falha, e um
evento por transição, com a verificação que a causou.
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta

import pytest

from middleware_monitor.core.models import (
    AmostraDeVerificacao,
    Equipamento,
    EventoDeEquipamento,
    VerificacaoDeEquipamento,
)
from middleware_monitor.domain.equipamentos import estados, lista, sondas
from middleware_monitor.integrations.network.base import ResultadoDaSonda
from middleware_monitor.jobs import monitor_equipamentos as job


class _Relogio:
    def __init__(self) -> None:
        self.t = datetime.now(UTC).replace(tzinfo=None)

    def __call__(self) -> datetime:
        return self.t


class _Roteiro:
    """``sondas.executar`` falso: responde conforme o tipo, e conta as chamadas."""

    def __init__(self) -> None:
        self.respostas: dict[str, bool] = {}
        self.chamadas: list[str] = []

    async def __call__(self, tipo, host, parametros, *, timeout_ping_ms):
        self.chamadas.append(tipo)
        if self.respostas.get(tipo, True):
            return ResultadoDaSonda(ok=True, valor=12)
        return ResultadoDaSonda(ok=False, erro=f"{tipo} sem resposta")


@pytest.fixture
def relogio(monkeypatch) -> _Relogio:
    r = _Relogio()
    monkeypatch.setattr(job, "_agora", r)
    return r


@pytest.fixture
def roteiro(monkeypatch) -> _Roteiro:
    r = _Roteiro()
    monkeypatch.setattr(sondas, "executar", r)
    return r


def _cadastrar(db, *verificacoes: dict, host: str = "10.9.0.5", tentativas: int | None = None) -> str:
    item = {"id": None, "ref": "noc-1", "tipo": "gravador", "nome": "NVR", "host": host}
    if tentativas is not None:
        item["tentativasParaCair"] = tentativas
    item["verificacoes"] = [{"id": None, "ref": f"v{i}", **v} for i, v in enumerate(verificacoes)]
    validado = lista.validar_para([item], lista.vista(db))
    g = lista.gravar(db, validado, datetime.now(UTC).replace(tzinfo=None))
    db.commit()
    return g.ids_por_ref["noc-1"]


async def _rodada(relogio: _Relogio) -> int:
    n = await job.run_monitor_equipamentos()
    relogio.t += timedelta(seconds=61)
    return n


def _eq(db, eid: str) -> Equipamento:
    db.expire_all()
    eq = db.get(Equipamento, eid)
    assert eq is not None
    return eq


def _eventos(db) -> list[EventoDeEquipamento]:
    db.expire_all()
    return list(db.query(EventoDeEquipamento).order_by(EventoDeEquipamento.id))


ICMP = {"tipo": "icmp"}
TCP = {"tipo": "tcp", "parametros": {"porta": 554}}


async def test_primeira_resposta_poe_no_ar_sem_evento(db, relogio, roteiro) -> None:
    eid = _cadastrar(db, ICMP)
    assert _eq(db, eid).estado == "desconhecido"
    assert await _rodada(relogio) == 1
    assert _eq(db, eid).estado == "no_ar"
    # desconhecido → no_ar é o primeiro ciclo, não notícia.
    assert _eventos(db) == []
    db.expire_all()
    amostras = db.query(AmostraDeVerificacao).all()
    assert [(a.ok, a.valor) for a in amostras] == [(True, 12)]


async def test_cai_so_na_terceira_falha_seguida_e_volta_na_primeira_resposta(db, relogio, roteiro) -> None:
    eid = _cadastrar(db, ICMP)
    await _rodada(relogio)
    roteiro.respostas["icmp"] = False
    await _rodada(relogio)
    await _rodada(relogio)
    eq = _eq(db, eid)
    assert eq.estado == "no_ar"
    assert eq.verificacoes[0].falhas_seguidas == 2
    assert eq.verificacoes[0].estado == "ok"
    assert _eventos(db) == []

    await _rodada(relogio)
    eq = _eq(db, eid)
    assert eq.estado == "fora"
    [caiu] = _eventos(db)
    assert (caiu.tipo, caiu.de, caiu.para) == ("equipamento.caiu", "no_ar", "fora")
    assert caiu.verificacao_id == eq.verificacoes[0].id
    assert "3 tentativas" in (caiu.detalhe or "") and "icmp sem resposta" in (caiu.detalhe or "")

    roteiro.respostas["icmp"] = True
    await _rodada(relogio)
    assert _eq(db, eid).estado == "no_ar"
    tipos = [e.tipo for e in _eventos(db)]
    assert tipos == ["equipamento.caiu", "equipamento.voltou"]
    assert "respondeu em 12 ms" in (_eventos(db)[-1].detalhe or "")


async def test_falha_isolada_nao_gera_evento_e_zera_na_resposta(db, relogio, roteiro) -> None:
    eid = _cadastrar(db, ICMP)
    await _rodada(relogio)
    for resposta in (False, False, True, False, False, True):
        roteiro.respostas["icmp"] = resposta
        await _rodada(relogio)
    assert _eq(db, eid).estado == "no_ar"
    assert _eventos(db) == []
    db.expire_all()
    # Toda execução vira amostra, inclusive as falhas que não derrubaram nada.
    assert db.query(AmostraDeVerificacao).count() == 7
    assert db.query(AmostraDeVerificacao).filter_by(ok=False).count() == 4


async def test_tentativas_para_cair_vem_do_equipamento(db, relogio, roteiro) -> None:
    eid = _cadastrar(db, ICMP, tentativas=1)
    await _rodada(relogio)
    roteiro.respostas["icmp"] = False
    await _rodada(relogio)
    assert _eq(db, eid).estado == "fora"


async def test_responde_ao_ping_e_a_porta_falha_e_degradado(db, relogio, roteiro) -> None:
    eid = _cadastrar(db, ICMP, TCP)
    await _rodada(relogio)
    assert _eq(db, eid).estado == "no_ar"
    roteiro.respostas["tcp"] = False
    for _ in range(3):
        await _rodada(relogio)
    eq = _eq(db, eid)
    assert eq.estado == "degradado"
    [degradou] = _eventos(db)
    assert (degradou.tipo, degradou.de, degradou.para) == ("equipamento.degradou", "no_ar", "degradado")
    tcp = next(v for v in eq.verificacoes if v.tipo == "tcp")
    assert degradou.verificacao_id == tcp.id

    # Agora o ping também cai: fora, e a causa é o ping.
    roteiro.respostas["icmp"] = False
    for _ in range(3):
        await _rodada(relogio)
    eq = _eq(db, eid)
    assert eq.estado == "fora"
    caiu = _eventos(db)[-1]
    assert (caiu.tipo, caiu.de) == ("equipamento.caiu", "degradado")
    assert caiu.verificacao_id == next(v.id for v in eq.verificacoes if v.tipo == "icmp")

    # Volta tudo: voltou (fora → no_ar).
    roteiro.respostas = {}
    await _rodada(relogio)
    assert _eq(db, eid).estado == "no_ar"
    assert _eventos(db)[-1].tipo == "equipamento.voltou"


async def test_normaliza_quando_a_porta_volta(db, relogio, roteiro) -> None:
    eid = _cadastrar(db, ICMP, TCP)
    await _rodada(relogio)
    roteiro.respostas["tcp"] = False
    for _ in range(3):
        await _rodada(relogio)
    roteiro.respostas["tcp"] = True
    await _rodada(relogio)
    assert _eq(db, eid).estado == "no_ar"
    normalizou = _eventos(db)[-1]
    assert (normalizou.tipo, normalizou.de, normalizou.para) == (
        "equipamento.normalizou",
        "degradado",
        "no_ar",
    )


async def test_sem_icmp_fora_e_todas_falhando(db, relogio, roteiro) -> None:
    eid = _cadastrar(db, TCP, {"tipo": "http"})
    await _rodada(relogio)
    roteiro.respostas["tcp"] = False
    for _ in range(3):
        await _rodada(relogio)
    assert _eq(db, eid).estado == "degradado"
    roteiro.respostas["http"] = False
    for _ in range(3):
        await _rodada(relogio)
    assert _eq(db, eid).estado == "fora"


async def test_nunca_respondeu_cai_depois_das_tentativas(db, relogio, roteiro) -> None:
    eid = _cadastrar(db, ICMP)
    roteiro.respostas["icmp"] = False
    await _rodada(relogio)
    await _rodada(relogio)
    assert _eq(db, eid).estado == "desconhecido"
    await _rodada(relogio)
    assert _eq(db, eid).estado == "fora"
    assert [(e.tipo, e.de) for e in _eventos(db)] == [("equipamento.caiu", "desconhecido")]


async def test_so_roda_a_verificacao_que_venceu(db, relogio, roteiro) -> None:
    _cadastrar(db, ICMP, {"tipo": "tcp", "parametros": {"porta": 22}, "intervaloS": 300})
    assert await job.run_monitor_equipamentos() == 2
    relogio.t += timedelta(seconds=61)
    assert await job.run_monitor_equipamentos() == 1
    assert roteiro.chamadas == ["icmp", "tcp", "icmp"]
    relogio.t += timedelta(seconds=5)
    assert await job.run_monitor_equipamentos() == 0


async def test_sem_equipamento_o_job_nao_faz_nada(db, relogio, roteiro) -> None:
    assert await job.run_monitor_equipamentos() == 0
    assert roteiro.chamadas == []


def test_estado_derivado() -> None:
    def vf(tipo: str, estado: str) -> VerificacaoDeEquipamento:
        return VerificacaoDeEquipamento(tipo=tipo, estado=estado)

    assert estados.estado_do_equipamento([]) == "desconhecido"
    assert estados.estado_do_equipamento([vf("icmp", "ok"), vf("tcp", "desconhecido")]) == "no_ar"
    assert estados.estado_do_equipamento([vf("icmp", "ok"), vf("http", "falha")]) == "degradado"
    assert estados.estado_do_equipamento([vf("icmp", "falha"), vf("http", "ok")]) == "fora"
    assert estados.estado_do_equipamento([vf("icmp", "desconhecido"), vf("http", "ok")]) == "desconhecido"
    assert estados.tipo_do_evento("no_ar", "desconhecido") is None
    assert estados.tipo_do_evento("desconhecido", "no_ar") is None
    assert estados.tipo_do_evento("fora", "degradado") == "equipamento.voltou"


async def test_de_ponta_a_ponta_com_porta_local_de_verdade(db, relogio) -> None:
    """Sem roteiro: a sonda TCP real contra um socket em 127.0.0.1 que depois fecha."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    porta = s.getsockname()[1]
    eid = _cadastrar(db, {"tipo": "tcp", "parametros": {"porta": porta}}, host="127.0.0.1")
    await _rodada(relogio)
    assert _eq(db, eid).estado == "no_ar"
    s.close()
    for _ in range(3):
        await _rodada(relogio)
    eq = _eq(db, eid)
    assert eq.estado == "fora"
    assert eq.verificacoes[0].ultimo_erro
    assert [e.tipo for e in _eventos(db)] == ["equipamento.caiu"]
