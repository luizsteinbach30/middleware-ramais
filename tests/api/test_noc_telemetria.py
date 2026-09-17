"""Telemetria para o NOC (v2.13.0) — o que vai, o que nunca vai, e o cursor.

O NOC é simulado com ``respx``. O que se prova aqui: o lote leva tudo o que os
webhooks levavam e mais; nenhuma senha sai; o cursor só anda com 202; NOC fora
não perde nada e a volta retoma de onde parou.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from middleware_monitor.core.models import (
    Collection,
    Device,
    DevicePing,
    ExtensionApplyRun,
    ExtensionApplyRunLine,
    ExtensionEnvironment,
    ExtensionLine,
    ExtensionStatusEvent,
)
from middleware_monitor.domain.noc import certificado, estado, telemetria
from middleware_monitor.jobs.noc_agent import run_noc_telemetria
from tests.api.test_noc_agent import CANAL, AutoridadeDeTeste, _enrolado_direto

URL = f"{CANAL}/agente/v1/telemetria"


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def _cache_limpo():
    certificado.limpar_cache_para_testes()
    yield
    certificado.limpar_cache_para_testes()


def _povoar(db) -> None:
    agora = _agora()
    d1 = Device(
        name="1001",
        ip="10.0.0.11",
        mac="AA:BB",
        model="UC924",
        logical_status="available",
        network_status="online",
        latency_ms=20,
        last_ping_at=agora,
        created_at=agora,
        updated_at=agora,
    )
    d2 = Device(
        name="1002",
        ip="10.0.0.12",
        logical_status="available",
        network_status="offline",
        last_ping_at=agora,
        created_at=agora,
        updated_at=agora,
    )
    db.add_all([d1, d2])
    db.flush()
    db.add_all(
        [
            # Antes da janela inicial: não vai no primeiro lote.
            DevicePing(device_id=d1.id, timestamp=agora - timedelta(hours=3), online=True, latency_ms=99),
            DevicePing(device_id=d1.id, timestamp=agora - timedelta(minutes=10), online=True, latency_ms=18),
            DevicePing(
                device_id=d2.id, timestamp=agora - timedelta(minutes=5), online=False, latency_ms=None
            ),
            ExtensionStatusEvent(
                ramal="1002",
                status="indisponivel",
                status_raw="UNAVAILABLE",
                received_at=agora - timedelta(minutes=4),
            ),
        ]
    )
    amb = ExtensionEnvironment(
        id="amb1",
        nome="LOJA 7",
        modelo_telefone="htek_uc924",
        config_padrao="{}",
        created_at=agora,
        updated_at=agora,
    )
    db.add(amb)
    db.add(
        ExtensionLine(
            id="l1",
            environment_id="amb1",
            numero_ramal="1001",
            ip="10.0.0.11",
            user_auth="1001",
            senha_sip="SENHA-QUE-NAO-SAI",
            ultimo_status="ok",
            ultima_aplicacao=agora,
            ultimo_hash_aplicado="abc",
            created_at=agora,
            updated_at=agora,
        )
    )
    run = ExtensionApplyRun(
        environment_id="amb1",
        started_at=agora - timedelta(minutes=30),
        finished_at=agora - timedelta(minutes=29),
        total=1,
        ok=1,
        falha=0,
    )
    db.add(run)
    db.flush()
    db.add(
        ExtensionApplyRunLine(
            run_id=run.id,
            numero_ramal="1001",
            ip="10.0.0.11",
            status_antes="pending",
            status_depois="ok",
            registro_sip="registered",
            created_at=agora,
        )
    )
    payload = [{"ramal": "1001", "status": "Available", "ip": "10.0.0.11", "token": "TOKEN-QUE-NAO-SAI"}]
    db.add(
        Collection(
            type="extensions",
            collected_at=agora,
            payload=json.dumps(payload),
            payload_hash="h",
            size_bytes=10,
        )
    )
    db.commit()


def _corpo(rota: respx.Route, indice: int = -1) -> dict:
    pedido = rota.calls[indice].request
    assert pedido.headers["content-encoding"] == "gzip"
    assert pedido.headers["idempotency-key"]
    return json.loads(gzip.decompress(pedido.content))


def test_o_lote_leva_tudo_e_nenhuma_senha(db) -> None:
    _povoar(db)
    lote, novos, mais = telemetria.montar_lote(db, telemetria.cursores(db))
    # A senha SIP viaja só na planilha do retrato (ADR 0012 do NOC); em nenhum outro lugar do lote.
    sem_planilha = {**lote, "ambientes": [{**a, "linhas": []} for a in lote["ambientes"]]}
    texto = json.dumps(sem_planilha)
    assert "SENHA-QUE-NAO-SAI" not in texto
    senhas = [ln["senhaSip"] for amb in lote["ambientes"] for ln in amb["linhas"]]
    assert "SENHA-QUE-NAO-SAI" in senhas
    assert "TOKEN-QUE-NAO-SAI" not in texto
    assert {d["ramal"] for d in lote["dispositivos"]} == {"1001", "1002"}
    # A janela inicial é de uma hora: a amostra de 3 h atrás fica de fora.
    assert [a["latenciaMs"] for a in lote["amostras"]] == [18, None]
    assert lote["eventos"][0]["status"] == "indisponivel"
    assert lote["perfis"] == [
        {
            "ramal": "1001",
            "ambiente": "LOJA 7",
            "ambienteId": "amb1",
            "modelo": "htek_uc924",
            "ip": "10.0.0.11",
            "status": "ok",
            "aplicadoEm": lote["perfis"][0]["aplicadoEm"],
            "hash": "abc",
            "erro": None,
            "modeloDetectado": None,
            "macDetectado": None,
        }
    ]
    assert lote["aplicacoes"][0]["ambienteId"] == "amb1"
    assert lote["aplicacoes"][0]["linhas"][0] == {
        "ramal": "1001",
        "ip": "10.0.0.11",
        "antes": "pending",
        "depois": "ok",
        "erro": None,
        "modelo": None,
        "registroSip": "registered",
    }
    assert lote["ramaisUscall"] == [{"ramal": "1001", "status": "Available", "ip": "10.0.0.11"}]
    assert mais is False
    assert novos["coleta"] > 0 and novos["aplicacoes"] > 0


def test_relatorio_ainda_rodando_segura_o_cursor(db) -> None:
    agora = _agora()
    db.add(
        ExtensionEnvironment(
            id="a", nome="A", modelo_telefone="x", config_padrao="{}", created_at=agora, updated_at=agora
        )
    )
    rodando = ExtensionApplyRun(environment_id="a", started_at=agora, total=1, ok=0, falha=0)
    db.add(rodando)
    db.flush()
    db.add(ExtensionApplyRun(environment_id="a", started_at=agora, finished_at=agora, total=1, ok=1, falha=0))
    db.commit()
    cur = {"amostras": 0, "eventos": 0, "aplicacoes": 0, "coleta": 0, "conexoes": 0}
    lote, novos, _ = telemetria.montar_lote(db, cur)
    assert lote["aplicacoes"] == []
    assert novos["aplicacoes"] == 0


@respx.mock
async def test_cursor_so_anda_com_202_e_retoma_depois_da_falha(db) -> None:
    _enrolado_direto(db, AutoridadeDeTeste())
    _povoar(db)
    rota = respx.post(URL).mock(return_value=httpx.Response(503, json={"mensagem": "NOC em manutenção"}))

    assert await run_noc_telemetria() == 0
    db.expire_all()
    parado = estado.carregar(db)
    assert parado.telemetria_detalhe == "NOC em manutenção"
    assert estado.carregar_cursores(db)["amostras"] is None

    rota.mock(return_value=httpx.Response(202, json={"recebido": True, "duplicado": False}))
    assert await run_noc_telemetria() == 1
    enviado = _corpo(rota)
    assert [a["latenciaMs"] for a in enviado["amostras"]] == [18, None]
    db.expire_all()
    assert estado.carregar(db).telemetria_detalhe is None
    assert estado.carregar(db).telemetria_enviada_em is not None

    # O ciclo seguinte leva só o que é novo — e o retrato inteiro de novo.
    agora = _agora()
    d1 = db.query(Device).filter_by(name="1001").one()
    db.add(DevicePing(device_id=d1.id, timestamp=agora, online=True, latency_ms=25))
    db.commit()
    assert await run_noc_telemetria() == 1
    segundo = _corpo(rota)
    assert [a["latenciaMs"] for a in segundo["amostras"]] == [25]
    assert segundo["eventos"] == [] and segundo["aplicacoes"] == [] and segundo["ramaisUscall"] is None
    assert len(segundo["dispositivos"]) == 2
    # O retrato dos ambientes e o coletor vão em todo lote (etapa I3 do NOC).
    assert [a["id"] for a in segundo["ambientes"]] == ["amb1"]
    assert segundo["coletor"][0]["estado"] == "sem_broker"
    assert (
        rota.calls[-1].request.headers["idempotency-key"] != rota.calls[-2].request.headers["idempotency-key"]
    )


@respx.mock
async def test_fila_grande_esvazia_em_varios_lotes_no_mesmo_ciclo(db, monkeypatch) -> None:
    _enrolado_direto(db, AutoridadeDeTeste())
    _povoar(db)
    monkeypatch.setattr(telemetria, "LIMITE_AMOSTRAS", 1)
    rota = respx.post(URL).mock(return_value=httpx.Response(202, json={"recebido": True}))
    assert await run_noc_telemetria() == 3
    assert [len(_corpo(rota, i)["amostras"]) for i in range(3)] == [1, 1, 0]


@respx.mock
async def test_202_sem_confirmacao_nao_e_entrega(db) -> None:
    _enrolado_direto(db, AutoridadeDeTeste())
    respx.post(URL).mock(return_value=httpx.Response(202, json={}))
    assert await run_noc_telemetria() == 0
    db.expire_all()
    assert "sem confirmar" in (estado.carregar(db).telemetria_detalhe or "")


async def test_sem_enrolamento_nao_sai_nada(db) -> None:
    _povoar(db)
    assert await run_noc_telemetria() == 0
