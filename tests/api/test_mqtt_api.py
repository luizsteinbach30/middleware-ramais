"""API do coletor MQTT: configuração do broker, ledger e comprovante."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from middleware_monitor.domain.auth.service import bootstrap_admin
from middleware_monitor.domain.mqtt import repository as repo

AGORA = datetime.now(UTC).replace(tzinfo=None)


def _authed(client, db) -> str:
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    r = client.post("/api/auth/login", json={"username": user.username, "password": plaintext})
    assert r.status_code == 200, r.json()
    return client.cookies.get("mm_csrf") or ""


def _broker_body(**over):
    body = {
        "nome": "EMQX Ambisec",
        "address_input": "emqx.ambimon.com",
        "host": "emqx.ambimon.com",
        "port": 8883,
        "transport": "tcp",
        "tls": True,
        "username": "log",
        "password": "log",
        "tls_verify": False,
        "tls_fingerprint": "9F:2A:00",
        "topics": ["v1/data/extenStatus/+"],
        "qos": 1,
        "enabled": True,
    }
    body.update(over)
    return body


def _gravar_mensagens(db, quantas: int = 3) -> None:
    repo.insert_messages(
        db,
        [
            {
                "broker_id": None,
                # Cronológica, como na vida real: id e hora crescem juntos.
                "received_at": AGORA - timedelta(minutes=quantas - i),
                "topic": f"v1/data/extenStatus/{100 + i}",
                "ramal": str(100 + i),
                "payload": (
                    '{"retorno": {"%s": {"status": "Ocupado", "ramal": "%s"}}}'.replace(
                        "%s", str(100 + i)
                    )
                ),
                "payload_bytes": 80,
                "qos": 1,
                "retained": False,
                "b64": False,
                "truncated": False,
                "event_at": None,
                "pinned": False,
            }
            for i in range(quantas)
        ],
    )
    db.commit()


def test_endpoints_exigem_autenticacao(client) -> None:
    assert client.get("/api/mqtt/status").status_code == 401
    assert client.get("/api/mqtt/messages").status_code == 401
    assert client.get("/api/mqtt/brokers").status_code == 401


def test_crud_do_broker_nunca_devolve_a_senha(client, db) -> None:
    csrf = _authed(client, db)
    r = client.post("/api/mqtt/brokers", json=_broker_body(), headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.json()
    criado = r.json()
    assert criado["password"] == "set"
    assert criado["client_id"].startswith("mwmonitor-")
    # Sessão durável é padrão: é ela que evita buraco no registro em reinício.
    assert criado["clean_session"] is False
    assert criado["topics"] == ["v1/data/extenStatus/+"]

    listagem = client.get("/api/mqtt/brokers").json()
    assert [b["password"] for b in listagem] == ["set"]

    # "set" no PUT preserva a senha gravada.
    r = client.put(
        f"/api/mqtt/brokers/{criado['id']}",
        json=_broker_body(nome="EMQX renomeado", password="set"),
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200 and r.json()["nome"] == "EMQX renomeado"
    broker = repo.get_broker(db, criado["id"])
    db.refresh(broker)
    assert repo.load_broker_password(broker) == "log"

    r = client.delete(f"/api/mqtt/brokers/{criado['id']}", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 204
    assert client.get("/api/mqtt/brokers").json() == []


def test_broker_sem_topico_valido_e_recusado(client, db) -> None:
    csrf = _authed(client, db)
    r = client.post("/api/mqtt/brokers", json=_broker_body(topics=[]), headers={"X-CSRF-Token": csrf})
    assert r.status_code == 422 and r.json()["detail"] == "topics_required"

    r = client.post(
        "/api/mqtt/brokers", json=_broker_body(topics=["v1/#/data"]), headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422
    assert r.json()["detail"].startswith("topic_invalid:")


def test_mutacao_exige_csrf(client, db) -> None:
    _authed(client, db)
    assert client.post("/api/mqtt/brokers", json=_broker_body()).status_code == 403


def test_busca_no_ledger_por_periodo_topico_e_texto(client, db) -> None:
    _authed(client, db)
    _gravar_mensagens(db, 3)

    r = client.get("/api/mqtt/messages", params={"last": "1h"})
    assert r.status_code == 200
    dados = r.json()
    assert dados["total"] == 3
    assert dados["items"][0]["preview"]

    r = client.get("/api/mqtt/messages", params={"topic": "v1/data/extenStatus/+"})
    assert r.json()["total"] == 3

    r = client.get("/api/mqtt/messages", params={"ramal": "101"})
    assert r.json()["total"] == 1

    r = client.get("/api/mqtt/messages", params={"contains": "Ocupado"})
    assert r.json()["total"] == 3

    r = client.get("/api/mqtt/messages", params={"topic": "v1/#/data"})
    assert r.status_code == 422


def test_detalhe_traz_o_payload_cru_e_o_formatado(client, db) -> None:
    _authed(client, db)
    _gravar_mensagens(db, 1)
    msg_id = client.get("/api/mqtt/messages").json()["items"][0]["id"]

    r = client.get(f"/api/mqtt/messages/{msg_id}")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["payload"].startswith("{")
    assert corpo["pretty"] and "retorno" in corpo["pretty"]
    assert client.get("/api/mqtt/messages/999999").status_code == 404


def test_fixar_evidencia_protege_da_retencao(client, db) -> None:
    csrf = _authed(client, db)
    _gravar_mensagens(db, 1)
    msg_id = client.get("/api/mqtt/messages").json()["items"][0]["id"]

    r = client.post(
        f"/api/mqtt/messages/{msg_id}/pin", json={"pinned": True}, headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200 and r.json() == {"pinned": True}

    apagadas = repo.purge_messages_by_age(db, AGORA + timedelta(days=1))
    db.commit()
    assert apagadas == 0
    assert client.get("/api/mqtt/messages", params={"pinned": True}).json()["total"] == 1


def test_comprovante_sai_como_anexo_de_texto(client, db) -> None:
    _authed(client, db)
    _gravar_mensagens(db, 1)
    msg_id = client.get("/api/mqtt/messages").json()["items"][0]["id"]

    r = client.get(f"/api/mqtt/messages/{msg_id}/comprovante")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    texto = r.text
    assert "COMPROVANTE DE RECEBIMENTO" in texto
    assert "v1/data/extenStatus/100" in texto
    assert "Cobertura do coletor" in texto


def test_cobertura_sem_historico_e_desconhecida(client, db) -> None:
    _authed(client, db)
    r = client.get("/api/mqtt/coverage", params={"last": "1h"})
    assert r.status_code == 200
    dados = r.json()
    assert dados["unknown"] is True
    assert dados["coverage_pct"] == 0.0


def test_cobertura_conta_o_tempo_conectado(client, db) -> None:
    _authed(client, db)
    repo.insert_connection_events(
        db,
        [
            {
                "broker_id": None,
                "timestamp": AGORA - timedelta(hours=3),
                "state": "connected",
                "detail": "conectado",
                "client_id": "c1",
                "endpoint": "ssl://emqx:8883",
            },
            {
                "broker_id": None,
                "timestamp": AGORA - timedelta(minutes=30),
                "state": "disconnected",
                "detail": "conexão perdida",
                "client_id": "c1",
                "endpoint": "ssl://emqx:8883",
            },
        ],
    )
    db.commit()

    dados = client.get("/api/mqtt/coverage", params={"last": "1h"}).json()
    assert dados["unknown"] is False
    assert 45.0 <= dados["coverage_pct"] <= 55.0
    assert dados["gaps"] and "perdida" in dados["gaps"][0]["detail"]


def test_status_reporta_configuracao_e_retencao(client, db) -> None:
    csrf = _authed(client, db)
    dados = client.get("/api/mqtt/status").json()
    assert dados["configured"] is False
    assert dados["retention_days"] == 7

    client.post("/api/mqtt/brokers", json=_broker_body(), headers={"X-CSRF-Token": csrf})
    _gravar_mensagens(db, 2)
    dados = client.get("/api/mqtt/status").json()
    assert dados["configured"] is True
    assert dados["stored_messages"] == 2
    assert dados["stored_payload_bytes"] == 160


def test_tela_de_configuracao_traz_a_secao_do_coletor(client, db) -> None:
    _authed(client, db)
    html = client.get("/config").text
    assert "Coletor de mensagens MQTT" in html
    assert 'id="mq-address"' in html and 'id="mq-discover"' in html
    assert "/static/js/pages/config_mqtt.js" in html
    # Retenção do ledger é editada junto com as demais, no mesmo formulário.
    assert 'id="f-mqtt_message_retention_days"' in html


def test_tela_de_mensagens_renderiza_com_filtros_e_cobertura(client, db) -> None:
    _authed(client, db)
    html = client.get("/mqtt-messages").text
    assert "Mensagens MQTT" in html
    assert 'id="mm-topic"' in html and 'id="mm-presets"' in html
    assert 'id="mm-coverage"' in html  # a faixa de cobertura acompanha o resultado
    assert "/static/js/pages/mqtt_messages.js" in html


def test_menu_lateral_tem_a_entrada_do_coletor(client, db) -> None:
    _authed(client, db)
    html = client.get("/mqtt-messages").text
    assert "Coletor MQTT" in html
    assert 'href="/mqtt-messages"' in html
    # Fica depois do Configurador de Ramais, como pedido.
    assert html.index("Configurador de Ramais") < html.index("Coletor MQTT")


def test_paginacao_para_tras_pelo_before_id(client, db) -> None:
    _authed(client, db)
    _gravar_mensagens(db, 5)

    pagina = client.get("/api/mqtt/messages", params={"limit": 2}).json()
    assert len(pagina["items"]) == 2
    assert pagina["total"] == 5
    assert pagina["truncated"] is True
    assert pagina["exact_total"] is True

    ultimo = pagina["items"][-1]["id"]
    mais = client.get("/api/mqtt/messages", params={"limit": 2, "before_id": ultimo}).json()
    assert [m["id"] for m in mais["items"]] == [ultimo - 1, ultimo - 2]


def test_modo_ao_vivo_traz_so_o_que_chegou_depois(client, db) -> None:
    _authed(client, db)
    _gravar_mensagens(db, 2)
    ultimo = client.get("/api/mqtt/messages").json()["items"][0]["id"]

    assert client.get("/api/mqtt/messages", params={"after_id": ultimo}).json()["items"] == []

    _gravar_mensagens(db, 1)
    novas = client.get("/api/mqtt/messages", params={"after_id": ultimo}).json()["items"]
    assert len(novas) == 1 and novas[0]["id"] > ultimo
