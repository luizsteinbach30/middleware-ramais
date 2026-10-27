"""Verbo ``chamadas_dos_ramais`` (2.19.1): o relatório de chamadas de teste do NOC.

Na ativação de loja o técnico liga de cada ramal novo para um número de teste; o NOC
pede aqui as chamadas que o coletor MQTT reconstruiu em ``extension_calls``. O que
estes testes seguram:

- o ramal casa como veio e, com ``-``, também pelo sufixo — o NOC pode mandar o
  usuário SIP com prefixo (``assaisp01-27611``), mas o PBX publica o número (``27611``);
- o período é fechado nas duas pontas e as horas saem em UTC com ``Z``;
- ``cortado`` diz que havia mais do que o ``limite``;
- ``coletorMqtt`` separa "cliente sem MQTT" de "nenhuma chamada";
- pedido fora de forma volta como recusa, sem tocar no banco.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import func, select

from middleware_monitor.core.models import ExtensionCall
from middleware_monitor.domain.mqtt import repository as mqtt_repo
from middleware_monitor.domain.noc import certificado, executor
from tests.api.test_noc_agent import CANAL, CREDENCIAL

BASE = datetime(2026, 10, 27, 13, 0, 0)


@pytest.fixture(autouse=True)
def _limpo():
    certificado.limpar_cache_para_testes()
    yield
    certificado.limpar_cache_para_testes()


def _chamada(db, ramal: str, minuto: int, **extra) -> ExtensionCall:
    inicio = BASE.replace(minute=minuto)
    c = ExtensionCall(
        ramal=ramal,
        direcao=extra.pop("direcao", "sainte"),
        numero=extra.pop("numero", "800"),
        uniqueid=extra.pop("uniqueid", None),
        started_at=inicio,
        answered_at=extra.pop("answered_at", inicio.replace(second=4)),
        ended_at=extra.pop("ended_at", inicio.replace(second=30)),
        ring_seconds=extra.pop("ring_seconds", 4),
        talk_seconds=extra.pop("talk_seconds", 26),
        outcome=extra.pop("outcome", "atendida"),
        last_event_id=0,
        created_at=inicio,
        updated_at=inicio,
    )
    db.add(c)
    return c


async def _pedir(pedido: dict, id_: str = "t1") -> dict:
    pronta = await executor.processar(
        {
            "id": id_,
            "tipo": "chamadas_dos_ramais",
            "raio": "LEITURA",
            "pedido": pedido,
            "pedidaPor": "ana@workconnect.com.br",
            "idempotencia": f"chave-{id_}",
            "leaseSegundos": 900,
        },
        canal=CANAL,
        credencial=CREDENCIAL,
    )
    assert pronta is not None
    return pronta.corpo


def _periodo(**extra) -> dict:
    return {"desde": "2026-10-27T13:00:00Z", "ate": "2026-10-27T13:59:59Z", **extra}


def _broker(db, enabled: bool = True) -> None:
    mqtt_repo.create_broker(
        db, nome="EMQX", address_input="emqx.exemplo", host="emqx.exemplo",
        port=1883, tls=False, username="", password_plain="", topics=["v1/data/extenStatus/#"],
        enabled=enabled,
    )
    db.commit()


def test_e_leitura_e_declarada_ao_noc(db) -> None:
    from middleware_monitor.domain.noc import manifesto

    acao = executor.ACOES["chamadas_dos_ramais"]
    assert acao.raio == executor.LEITURA
    assert acao.obrigatorios == {"ramais", "desde", "ate"}
    assert acao.opcionais == {"limite"}
    assert "chamadas_dos_ramais" in manifesto.montar(db)["acoes"]


async def test_filtra_por_ramal_e_casa_o_usuario_sip_pelo_sufixo(db) -> None:
    _chamada(db, "27611", 5, uniqueid="u-1")
    _chamada(db, "27612", 6)
    _chamada(db, "0119", 7)
    _chamada(db, "99999", 8)  # não pedido
    db.commit()

    corpo = await _pedir(_periodo(ramais=["assaisp01-27611", "27612", "0119"]))

    assert corpo["ok"] is True
    r = corpo["resultado"]
    assert [c["ramal"] for c in r["chamadas"]] == ["27611", "27612", "0119"]
    assert r["cortado"] is False
    primeira = r["chamadas"][0]
    assert primeira == {
        "ramal": "27611",
        "direcao": "sainte",
        "numero": "800",
        "uniqueid": "u-1",
        "inicio": "2026-10-27T13:05:00Z",
        "atendidaEm": "2026-10-27T13:05:04Z",
        "fimEm": "2026-10-27T13:05:30Z",
        "toqueS": 4,
        "conversaS": 26,
        "resultado": "atendida",
    }


async def test_o_item_tambem_casa_como_veio(db) -> None:
    """PBX que publica o próprio usuário com hífen continua achado pelo valor inteiro."""
    _chamada(db, "loja-10", 5)
    _chamada(db, "10", 6)
    db.commit()

    r = (await _pedir(_periodo(ramais=["loja-10"])))["resultado"]
    assert [c["ramal"] for c in r["chamadas"]] == ["loja-10", "10"]


async def test_periodo_fechado_e_ordem_por_inicio(db) -> None:
    _chamada(db, "27611", 30)
    _chamada(db, "27611", 0)  # exatamente em desde
    _chamada(db, "27611", 59, answered_at=None, ended_at=None, talk_seconds=None,
             ring_seconds=None, outcome="em_curso")
    db.add(ExtensionCall(
        ramal="27611", direcao="sainte", started_at=datetime(2026, 10, 27, 14, 0, 0),
        outcome="perdida", last_event_id=0, created_at=BASE, updated_at=BASE,
    ))  # depois de ate
    db.add(ExtensionCall(
        ramal="27611", direcao="sainte", started_at=datetime(2026, 10, 27, 12, 59, 59),
        outcome="perdida", last_event_id=0, created_at=BASE, updated_at=BASE,
    ))  # antes de desde
    db.commit()

    r = (await _pedir(_periodo(ramais=["27611"])))["resultado"]
    assert [c["inicio"] for c in r["chamadas"]] == [
        "2026-10-27T13:00:00Z", "2026-10-27T13:30:00Z", "2026-10-27T13:59:00Z",
    ]
    em_curso = r["chamadas"][-1]
    assert em_curso["resultado"] == "em_curso"
    assert em_curso["fimEm"] is None and em_curso["atendidaEm"] is None


async def test_periodo_com_fuso_e_convertido_para_utc(db) -> None:
    """``started_at`` é UTC sem fuso; 10:05-03:00 é 13:05 UTC."""
    _chamada(db, "27611", 5)
    db.commit()
    r = (await _pedir({"ramais": ["27611"], "desde": "2026-10-27T10:04:00-03:00",
                       "ate": "2026-10-27T10:06:00-03:00"}))["resultado"]
    assert len(r["chamadas"]) == 1
    # Sem fuso no texto: já é UTC.
    r = (await _pedir({"ramais": ["27611"], "desde": "2026-10-27T10:04:00",
                       "ate": "2026-10-27T10:06:00"}, id_="t2"))["resultado"]
    assert r["chamadas"] == []


async def test_limite_corta_e_avisa(db) -> None:
    for minuto in range(5):
        _chamada(db, "27611", minuto)
    db.commit()

    r = (await _pedir(_periodo(ramais=["27611"], limite=3)))["resultado"]
    assert [c["inicio"][14:16] for c in r["chamadas"]] == ["00", "01", "02"]
    assert r["cortado"] is True

    r = (await _pedir(_periodo(ramais=["27611"], limite=5), id_="t2"))["resultado"]
    assert len(r["chamadas"]) == 5
    assert r["cortado"] is False


async def test_coletor_mqtt_separa_sem_mqtt_de_nenhuma_chamada(db) -> None:
    r = (await _pedir(_periodo(ramais=["27611"])))["resultado"]
    assert r == {"chamadas": [], "cortado": False, "coletorMqtt": False}

    _broker(db, enabled=False)
    r = (await _pedir(_periodo(ramais=["27611"]), id_="t2"))["resultado"]
    assert r["coletorMqtt"] is False  # broker cadastrado e desligado não coleta

    _broker(db, enabled=True)
    r = (await _pedir(_periodo(ramais=["27611"]), id_="t3"))["resultado"]
    assert r == {"chamadas": [], "cortado": False, "coletorMqtt": True}


@pytest.mark.parametrize(
    ("pedido", "trecho"),
    [
        (_periodo(ramais=[]), "ramais"),
        (_periodo(ramais="27611"), "ramais"),
        (_periodo(ramais=["27 611; rm"]), "ramais"),
        (_periodo(ramais=[27611]), "ramais"),
        (_periodo(ramais=["1"] * 501), "ramais"),
        ({"ramais": ["27611"], "desde": "ontem", "ate": "2026-10-27T13:59:59Z"}, "ISO-8601"),
        ({"ramais": ["27611"], "desde": "2026-10-27T13:00:00Z", "ate": 1730000000}, "ISO-8601"),
        ({"ramais": ["27611"], "desde": "2026-10-27T14:00:00Z", "ate": "2026-10-27T13:00:00Z"}, "anterior"),
        (_periodo(ramais=["27611"], limite=0), "limite"),
        (_periodo(ramais=["27611"], limite=5001), "limite"),
        (_periodo(ramais=["27611"], limite="10"), "limite"),
        (_periodo(ramais=["27611"], limite=True), "limite"),
    ],
)
async def test_pedido_fora_de_forma_e_recusado(db, pedido, trecho) -> None:
    _chamada(db, "27611", 5)
    db.commit()
    corpo = await _pedir(pedido)
    assert corpo["ok"] is False
    assert corpo["naoSuportado"] is True
    assert corpo["resultado"] == {"recusa": "VALOR_INVALIDO"}
    assert trecho in corpo["erro"]


@pytest.mark.parametrize(
    ("pedido", "trecho"),
    [
        ({"ramais": ["27611"], "desde": "2026-10-27T13:00:00Z"}, "precisa de"),
        (_periodo(ramais=["27611"], extra=1), "não aceita"),
    ],
)
def test_campos_faltando_ou_a_mais_sao_recusados_antes(pedido, trecho) -> None:
    recusa = executor.conferir("chamadas_dos_ramais", "LEITURA", pedido)
    assert recusa is not None and trecho in (recusa.erro or "")
    assert "aqui é LEITURA" in (
        executor.conferir("chamadas_dos_ramais", "ESCRITA_REVERSIVEL", _periodo(ramais=["1"])).erro or ""
    )


async def test_leitura_nao_grava_em_extension_calls(db) -> None:
    _chamada(db, "27611", 5)
    db.commit()
    await _pedir(_periodo(ramais=["27611"]))
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(ExtensionCall)) == 1
