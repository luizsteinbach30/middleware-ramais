"""API do painel ao vivo: `/api/mqtt/live`.

A tela recarrega a cada 2,5 s — o endpoint tem de responder o estado corrente
sem depender de varredura de tabela, e tem de dizer com todas as letras quando
o coletor **não** está rodando: uma grade toda verde vinda de um coletor morto
seria pior do que tela vazia.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from middleware_monitor.core.models import Device
from middleware_monitor.domain.auth.service import bootstrap_admin
from middleware_monitor.domain.mqtt import realtime
from middleware_monitor.domain.mqtt.service import get_ingestor

BASE = datetime(2026, 8, 21, 12, 0, 0)


def _authed(client, db) -> None:
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    r = client.post("/api/auth/login", json={"username": user.username, "password": plaintext})
    assert r.status_code == 200, r.json()


def _sample(ramal: str, status: str, **over: object) -> realtime.Sample:
    dados: dict[str, object] = {
        "ramal": ramal, "status": status, "status_raw": status.title(),
        "numero": "", "uniqueid": None, "event_at": None,
        "received_at": BASE, "call_started_at": None, "message_id": None,
    }
    dados.update(over)
    return realtime.Sample(**dados)  # type: ignore[arg-type]


def test_live_exige_sessao(client) -> None:
    assert client.get("/api/mqtt/live").status_code == 401


def test_live_sem_broker_avisa_que_nao_ha_o_que_coletar(client, db) -> None:
    _authed(client, db)
    r = client.get("/api/mqtt/live")
    assert r.status_code == 200
    body = r.json()
    # `running` é o worker de gravação (sobe sempre); `configured` é o que a
    # tela usa para mandar o operador cadastrar um broker.
    assert body["running"] is True
    assert body["configured"] is False
    assert body["extensions"] == []
    assert body["counts"] == {}


def test_live_devolve_o_estado_corrente_de_cada_ramal(client, db) -> None:
    _authed(client, db)
    ing = get_ingestor()
    ing.state = realtime.RealtimeState()
    ing.state._primed = True
    ing.state.classify([
        _sample("0119", "ocupado", numero="800"),
        _sample("0120", "disponivel"),
        _sample("0121", "indisponivel"),
    ])

    r = client.get("/api/mqtt/live")
    body = r.json()
    assert body["counts"] == {"ocupado": 1, "disponivel": 1, "indisponivel": 1}
    # Ordenação por ramal: a grade da tela não pode dançar a cada recarga.
    assert [e["ramal"] for e in body["extensions"]] == ["0119", "0120", "0121"]
    ocupado = body["extensions"][0]
    assert ocupado["status"] == "ocupado" and ocupado["numero"] == "800"
    assert ocupado["device_id"] is None  # a coleta REST ainda não criou o telefone


def test_live_casa_o_ramal_com_o_device_para_trazer_ip_e_rede(client, db) -> None:
    _authed(client, db)
    dev = Device(
        name="0119", ip="192.168.0.48", logical_status="available",
        network_status="online", created_at=BASE, updated_at=BASE,
    )
    db.add(dev)
    db.commit()

    ing = get_ingestor()
    ing.state = realtime.RealtimeState()
    ing.state._primed = True
    ing.state.classify([_sample("0119", "tocando", numero="800")])

    body = client.get("/api/mqtt/live").json()
    ramal = body["extensions"][0]
    # IP e estado de rede não existem no payload MQTT: vêm do device.
    assert ramal["device_id"] == dev.id
    assert ramal["ip"] == "192.168.0.48"
    assert ramal["network_status"] == "online"


def test_live_traz_a_fita_de_transicoes_mais_recente_primeiro(client, db) -> None:
    _authed(client, db)
    st = realtime.RealtimeState()
    st._primed = True
    transicoes, _ = st.classify([
        _sample("0119", "tocando", numero="800"),
        _sample("0119", "ocupado", numero="800", received_at=BASE + timedelta(seconds=4)),
    ])
    realtime.insert_transitions(db, transicoes)
    db.commit()

    body = client.get("/api/mqtt/live?transicoes=10").json()
    assert [t["status"] for t in body["transitions"]] == ["ocupado", "tocando"]

    # transicoes=0 serve para a tela pedir só a grade, sem tocar no banco.
    assert client.get("/api/mqtt/live?transicoes=0").json()["transitions"] == []
