"""Normalização do MQTT: transições, estado do ramal e reflexo no device.

Duas promessas são protegidas aqui:

1. **não repetir** — o publicador reenvia o mesmo status a cada varredura, e o
   que vira linha no banco é só o que mudou;
2. **não reaplicar configuração em telefone que está em conversa** — ramal
   tocando ou falando está registrado no PBX, e `jobs/monitor_devices` usa
   exatamente `logical_status='unavailable'` como sinal de "config perdida".
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from middleware_monitor.core.models import Device, ExtensionStatusEvent
from middleware_monitor.domain.mqtt import realtime
from middleware_monitor.domain.mqtt import repository as repo
from middleware_monitor.domain.mqtt.parser import (
    logical_from_status,
    normalize_status,
    parse_extension_payload,
)

BASE = datetime(2026, 8, 21, 12, 0, 0)


def _sample(ramal: str, status: str, **over: object) -> realtime.Sample:
    dados: dict[str, object] = {
        "ramal": ramal,
        "status": normalize_status(status),
        "status_raw": status,
        "numero": "",
        "uniqueid": None,
        "event_at": None,
        "received_at": BASE,
        "call_started_at": None,
        "message_id": None,
    }
    dados.update(over)
    return realtime.Sample(**dados)  # type: ignore[arg-type]


def _device(db: Session, nome: str, **over: object) -> Device:
    campos: dict[str, object] = {
        "name": nome, "ip": "192.168.0.10", "logical_status": "unknown",
        "network_status": "online", "created_at": BASE, "updated_at": BASE,
    }
    campos.update(over)
    dev = Device(**campos)
    db.add(dev)
    db.flush()
    return dev


# ── normalização ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("Disponivel", "disponivel"),
        ("Disponível", "disponivel"),   # o publicador acentua às vezes
        ("INDISPONIVEL", "indisponivel"),
        ("Tocando", "tocando"),
        ("Ocupado", "ocupado"),
        ("Discando", "discando"),
        ("", "desconhecido"),
        (None, "desconhecido"),
        ("Estacionada", "desconhecido"),  # status novo aparece como tal
    ],
)
def test_status_normaliza_acento_e_caixa(bruto: str | None, esperado: str) -> None:
    assert normalize_status(bruto) == esperado


@pytest.mark.parametrize(
    ("status", "logico"),
    [
        ("disponivel", "available"),
        # As três em uso continuam "registrado": só quem registrou pode tocar
        # ou falar. Marcá-las indisponível reaplicaria config durante a ligação.
        ("tocando", "available"),
        ("discando", "available"),
        ("ocupado", "available"),
        ("indisponivel", "unavailable"),
        ("desconhecido", None),
    ],
)
def test_apenas_indisponivel_derruba_o_estado_logico(status: str, logico: str | None) -> None:
    assert logical_from_status(status) == logico


# ── transições ───────────────────────────────────────────────────────────────


def test_repeticao_do_mesmo_estado_nao_vira_linha() -> None:
    st = realtime.RealtimeState()
    st._primed = True
    primeira, _ = st.classify([_sample("0119", "Disponivel")])
    assert len(primeira) == 1

    repetida, _ = st.classify([_sample("0119", "Disponivel", received_at=BASE + timedelta(seconds=5))])
    assert repetida == []

    mudou, _ = st.classify(
        [_sample("0119", "Tocando", numero="800", received_at=BASE + timedelta(seconds=10))]
    )
    assert [s.status for s in mudou] == ["tocando"]


def test_duas_chamadas_seguidas_para_o_mesmo_numero_sao_duas_transicoes() -> None:
    """Sem o horário de início na chave, a segunda chamada sumiria.

    ``duracao`` no payload é o início da chamada em curso — é o que distingue
    dois trechos com o mesmo status e o mesmo número.
    """
    st = realtime.RealtimeState()
    st._primed = True
    inicio_1 = BASE
    inicio_2 = BASE + timedelta(seconds=30)
    st.classify([_sample("0119", "Ocupado", numero="800", call_started_at=inicio_1)])
    segunda, _ = st.classify(
        [_sample("0119", "Ocupado", numero="800", call_started_at=inicio_2,
                 received_at=BASE + timedelta(seconds=31))]
    )
    assert len(segunda) == 1


def test_device_e_tocado_de_novo_depois_de_um_minuto_parado() -> None:
    """Ramal parado em Disponivel ainda precisa manter ``last_seen_at`` vivo."""
    st = realtime.RealtimeState()
    st._primed = True
    st.classify([_sample("0119", "Disponivel")])

    _, cedo = st.classify([_sample("0119", "Disponivel", received_at=BASE + timedelta(seconds=30))])
    assert cedo == []

    _, tarde = st.classify([_sample("0119", "Disponivel", received_at=BASE + timedelta(seconds=61))])
    assert [s.ramal for s in tarde] == ["0119"]


def test_falha_de_gravacao_desfaz_o_cache_para_a_transicao_nao_sumir() -> None:
    # Sem o rollback, a transição ficaria marcada como conhecida e nunca mais
    # seria gravada — o ramal congelaria no painel até mudar de novo.
    st = realtime.RealtimeState()
    st._primed = True
    transicoes, _ = st.classify([_sample("0119", "Tocando", numero="800")])
    st.rollback(transicoes)
    de_novo, _ = st.classify([_sample("0119", "Tocando", numero="800")])
    assert len(de_novo) == 1


def test_reinicio_do_servico_nao_inventa_transicao(db: Session) -> None:
    st = realtime.RealtimeState()
    st._primed = True
    transicoes, _ = st.classify([_sample("0119", "Ocupado", numero="800")])
    realtime.insert_transitions(db, transicoes)
    db.commit()

    # Serviço reinicia: estado novo, banco cheio.
    depois = realtime.RealtimeState()
    depois.prime(db)
    assert depois.counts() == {"ocupado": 1}
    repetida, _ = depois.classify(
        [_sample("0119", "Ocupado", numero="800", received_at=BASE + timedelta(seconds=5))]
    )
    assert repetida == []


# ── reflexo no device ────────────────────────────────────────────────────────


def test_ramal_em_conversa_continua_registrado_no_device(db: Session) -> None:
    dev = _device(db, "0119")
    realtime.touch_devices(db, [_sample("0119", "Ocupado", numero="800")])
    db.commit()
    db.refresh(dev)
    assert dev.telephony_status == "ocupado"
    assert dev.telephony_numero == "800"
    # O que `monitor_devices` lê para decidir reaplicar configuração:
    assert dev.logical_status == "available"
    assert dev.status_source == "mqtt"
    assert dev.last_seen_at == BASE


def test_ramal_indisponivel_marca_o_device(db: Session) -> None:
    dev = _device(db, "0119", logical_status="available")
    realtime.touch_devices(db, [_sample("0119", "Indisponivel")])
    db.commit()
    db.refresh(dev)
    assert dev.logical_status == "unavailable"
    assert dev.telephony_status == "indisponivel"


def test_status_desconhecido_nao_mexe_no_estado_logico(db: Session) -> None:
    dev = _device(db, "0119", logical_status="available")
    realtime.touch_devices(db, [_sample("0119", "Estacionada")])
    db.commit()
    db.refresh(dev)
    assert dev.logical_status == "available"       # intocado
    assert dev.telephony_status == "desconhecido"  # mas visível na tela


def test_ramal_sem_device_nao_cria_device(db: Session) -> None:
    # O payload MQTT não tem IP nem MAC: device sem endereço quebraria o ping e
    # o Configurador. Quem cria continua sendo a coleta REST.
    realtime.touch_devices(db, [_sample("9999", "Disponivel")])
    db.commit()
    assert db.scalar(select(Device).where(Device.name == "9999")) is None


# ── retenção ─────────────────────────────────────────────────────────────────


def test_retencao_apaga_transicao_velha(db: Session) -> None:
    st = realtime.RealtimeState()
    st._primed = True
    velha, _ = st.classify([_sample("0119", "Ocupado", received_at=BASE - timedelta(days=30))])
    nova, _ = st.classify([_sample("0120", "Ocupado", received_at=BASE)])
    realtime.insert_transitions(db, velha + nova)
    db.commit()

    apagadas = realtime.purge_status_events(db, BASE - timedelta(days=7))
    db.commit()
    assert apagadas == 1
    restantes = db.scalars(select(ExtensionStatusEvent.ramal)).all()
    assert list(restantes) == ["0120"]


# ── ponta a ponta com payload real ───────────────────────────────────────────


def test_payload_real_vira_transicao_apontando_para_o_comprovante(db: Session) -> None:
    payload = (
        '{"retorno": {"0119": {"status": "Ocupado", "ramal": "0119", '
        '"data": "2026-08-21 09:00:01.256211", "duracao": "2026-08-21 08:59:58", '
        '"numero": "800", "uniqueid": "1787158106.5138"}}}'
    )
    msg_id = repo.insert_messages(
        db,
        [{
            "broker_id": None, "received_at": BASE,
            "topic": "v1/data/extenStatus/0119", "ramal": "0119",
            "payload": payload, "payload_bytes": len(payload), "qos": 1,
            "retained": False, "b64": False, "truncated": False,
            "event_at": None, "pinned": False,
        }],
    )[0]
    amostras = realtime.samples_from(parse_extension_payload(payload), BASE, message_id=msg_id)
    st = realtime.RealtimeState()
    st._primed = True
    transicoes, _ = st.classify(amostras)
    realtime.insert_transitions(db, transicoes)
    db.commit()

    ev = db.scalars(select(ExtensionStatusEvent)).one()
    assert ev.ramal == "0119" and ev.status == "ocupado"
    assert ev.status_raw == "Ocupado"  # o que o publicador escreveu fica registrado
    assert ev.numero == "800" and ev.uniqueid == "1787158106.5138"
    assert ev.message_id == msg_id  # é por aqui que se chega à mensagem crua
    assert ev.event_at is not None and ev.call_started_at is not None


def test_coletor_grava_ledger_transicao_e_device_na_mesma_passagem(db: Session) -> None:
    """O caminho inteiro do coletor, como ele roda em produção.

    Uma mensagem entra pelo callback do paho e, ao fim do ciclo de gravação,
    tem de existir: a linha crua no ledger (o comprovante), a transição
    apontando para ela, e o device com o estado de agora. Se qualquer um dos
    três ficar para trás, o painel e a prova contam histórias diferentes.
    """
    import asyncio

    from middleware_monitor.core.db import session_factory
    from middleware_monitor.core.models import MqttMessage
    from middleware_monitor.domain.mqtt.service import MqttIngestor

    _device(db, "0119")
    db.commit()

    ing = MqttIngestor(db_factory=session_factory)
    entregar = ing._make_on_message(broker_id=None, max_payload_kb=0)
    corpo = (
        '{"retorno": {"0119": {"status": "Tocando", "ramal": "0119", '
        '"data": "2026-08-21 09:00:01.256211", "numero": "800"}}}'
    )
    entregar("v1/data/extenStatus/0119", corpo.encode(), 1, False)
    # A varredura seguinte repete o mesmo estado: não pode virar segunda linha.
    entregar("v1/data/extenStatus/0119", corpo.encode(), 1, False)
    asyncio.run(ing._flush())

    assert db.scalar(select(func.count(MqttMessage.id))) == 2  # o ledger guarda as duas
    eventos = db.scalars(select(ExtensionStatusEvent)).all()
    assert len(eventos) == 1                                   # a transição, uma só
    assert eventos[0].status == "tocando"
    assert eventos[0].message_id is not None

    dev = db.scalar(select(Device).where(Device.name == "0119"))
    assert dev is not None
    assert dev.telephony_status == "tocando" and dev.telephony_numero == "800"
    assert dev.logical_status == "available" and dev.status_source == "mqtt"
    assert ing.transitions == 1 and ing.persisted == 2
