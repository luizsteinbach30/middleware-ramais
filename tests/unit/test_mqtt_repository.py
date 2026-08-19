"""Ledger MQTT: gravação em lote, busca e retenção.

A regra que mais importa aqui: mensagem fixada como evidência não é apagada
por retenção nenhuma.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from middleware_monitor.core.models import MqttMessage
from middleware_monitor.domain.mqtt import repository as repo

BASE = datetime(2026, 8, 19, 12, 0, 0)


def _msg(db: Session, **over: object) -> dict[str, object]:
    linha: dict[str, object] = {
        "broker_id": None,
        "received_at": BASE,
        "topic": "v1/data/extenStatus/0119",
        "ramal": "0119",
        "payload": '{"retorno": {}}',
        "payload_bytes": 100,
        "qos": 1,
        "retained": False,
        "b64": False,
        "truncated": False,
        "event_at": None,
        "pinned": False,
    }
    linha.update(over)
    return linha


def test_broker_guarda_a_senha_cifrada(db: Session) -> None:
    b = repo.create_broker(
        db, nome="EMQX", address_input="emqx.ambimon.com", host="emqx.ambimon.com",
        port=8883, tls=True, username="log", password_plain="senha-secreta",
        topics=["v1/data/#"],
    )
    db.commit()
    assert "senha-secreta" not in b.password  # nunca em texto claro no banco
    assert repo.load_broker_password(b) == "senha-secreta"
    assert repo.broker_topics(b) == ["v1/data/#"]
    assert b.client_id.startswith("mwmonitor-")


def test_update_mantem_a_senha_quando_nao_vem_no_payload(db: Session) -> None:
    b = repo.create_broker(
        db, nome="EMQX", address_input="h", host="h", port=1883,
        password_plain="original", topics=["#"],
    )
    db.commit()
    repo.update_broker(db, b, nome="EMQX 2", password_plain=None)
    db.commit()
    assert repo.load_broker_password(b) == "original"
    repo.update_broker(db, b, password_plain="")
    db.commit()
    assert repo.load_broker_password(b) == ""


def test_busca_por_janela_topico_e_texto(db: Session) -> None:
    repo.insert_messages(
        db,
        [
            _msg(db, received_at=BASE, topic="v1/data/extenStatus/0119", payload="Ocupado"),
            _msg(db, received_at=BASE + timedelta(minutes=5), topic="v1/data/cdr",
                 ramal=None, payload="outra coisa"),
            _msg(db, received_at=BASE + timedelta(hours=2),
                 topic="v1/data/extenStatus/0307", ramal="0307", payload="Tocando"),
        ],
    )
    db.commit()

    itens, total = repo.search_messages(db, since=BASE, until=BASE + timedelta(hours=1))
    assert total == 2

    itens, total = repo.search_messages(db, topic_filter="v1/data/extenStatus/+")
    assert total == 2
    assert all(i.topic.startswith("v1/data/extenStatus/") for i in itens)

    itens, total = repo.search_messages(db, ramal="0307")
    assert total == 1 and itens[0].ramal == "0307"

    itens, total = repo.search_messages(db, contains="ocupado")  # sem diferenciar caixa
    assert total == 1


def test_retencao_por_idade_preserva_o_que_esta_fixado(db: Session) -> None:
    antiga = BASE - timedelta(days=30)
    repo.insert_messages(
        db,
        [
            _msg(db, received_at=antiga, payload="velha"),
            _msg(db, received_at=antiga, payload="evidencia", pinned=True),
            _msg(db, received_at=BASE, payload="nova"),
        ],
    )
    db.commit()

    apagadas = repo.purge_messages_by_age(db, BASE - timedelta(days=7))
    db.commit()
    assert apagadas == 1
    restantes = {m.payload for m in db.query(MqttMessage).all()}
    assert restantes == {"evidencia", "nova"}


def test_retencao_por_espaco_apaga_das_mais_antigas_para_as_novas(db: Session) -> None:
    repo.insert_messages(
        db,
        [
            _msg(db, received_at=BASE - timedelta(days=i), payload=f"m{i}", payload_bytes=1024)
            for i in range(10)
        ],
    )
    db.commit()
    apagadas = repo.purge_messages_by_size(db, 5 * 1024)
    db.commit()
    assert apagadas == 5
    assert repo.payload_bytes_total(db) <= 5 * 1024
    # As que sobraram são as mais recentes.
    assert {m.payload for m in db.query(MqttMessage).all()} == {"m0", "m1", "m2", "m3", "m4"}


def test_retencao_por_espaco_desligada_nao_apaga_nada(db: Session) -> None:
    repo.insert_messages(db, [_msg(db, payload_bytes=999_999)])
    db.commit()
    assert repo.purge_messages_by_size(db, 0) == 0


def test_fixar_e_desfixar_evidencia(db: Session) -> None:
    repo.insert_messages(db, [_msg(db)])
    db.commit()
    msg = db.query(MqttMessage).one()
    assert repo.set_pinned(db, msg.id, True) is True
    db.commit()
    assert db.get(MqttMessage, msg.id).pinned is True
    assert repo.set_pinned(db, 999_999, True) is False
