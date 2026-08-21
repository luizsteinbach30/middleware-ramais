"""Reconhecimento do payload de status de ramal.

As mensagens da fixture são captura real do broker do cliente
(2026-08-19, 74 ramais) — nada aqui é payload inventado.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from middleware_monitor.domain.mqtt.parser import (
    parse_extension_payload,
    parse_pbx_datetime,
    ramal_from_topic,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mqtt_extenstatus.jsonl"


def _mensagens() -> list[dict[str, object]]:
    linhas = FIXTURE.read_text(encoding="utf-8").splitlines()
    return [json.loads(linha) for linha in linhas if linha.strip()]


def test_reconhece_todas_as_mensagens_da_captura_real() -> None:
    msgs = _mensagens()
    assert msgs, "fixture vazia"
    for m in msgs:
        status = parse_extension_payload(str(m["payload"]))
        assert len(status) == 1, m["topic"]
        s = status[0]
        assert s.ramal == str(m["topic"]).rsplit("/", 1)[-1]
        assert s.status in ("Disponivel", "Indisponivel", "Tocando", "Ocupado", "Discando")
        assert s.event_at is not None


def test_uniqueid_ausente_nao_invalida_a_mensagem() -> None:
    # Nem todo ramal publica uniqueid — na captura, ~46% não publicam.
    sem_uid = [
        m for m in _mensagens() if "uniqueid" not in json.loads(str(m["payload"]))["retorno"].popitem()[1]
    ]
    assert sem_uid, "fixture deveria conter mensagens sem uniqueid"
    s = parse_extension_payload(str(sem_uid[0]["payload"]))[0]
    assert s.uniqueid is None
    assert s.ramal and s.status


def test_reconhecimento_e_pelo_formato_nao_pelo_topico() -> None:
    # O publicador pode renomear o tópico; o corpo é o que identifica.
    corpo = str(_mensagens()[0]["payload"])
    assert parse_extension_payload(corpo)  # mesmo sem informar tópico algum


@pytest.mark.parametrize(
    "corpo",
    [
        "",
        "   ",
        "não é json",
        "[]",
        '{"outra_coisa": 1}',
        '{"retorno": []}',
        '{"retorno": {"0119": {"status": "Ocupado"}}}',  # falta "ramal"
    ],
)
def test_payload_nao_reconhecido_nao_levanta(corpo: str) -> None:
    # Payload estranho é só payload não reconhecido: a mensagem crua já está
    # gravada, e é ela que serve de prova.
    assert parse_extension_payload(corpo) == []


def test_campo_ramal_vence_a_chave_do_dicionario() -> None:
    corpo = '{"retorno": {"chave-errada": {"status": "Ocupado", "ramal": "0119"}}}'
    assert parse_extension_payload(corpo)[0].ramal == "0119"


def test_duracao_e_o_inicio_da_chamada_nao_uma_duracao() -> None:
    corpo = (
        '{"retorno": {"0119": {"status": "Ocupado", "ramal": "0119", '
        '"data": "2026-08-19 13:51:01.256211", "duracao": "2026-08-19 13:50:58", '
        '"numero": "800"}}}'
    )
    s = parse_extension_payload(corpo)[0]
    assert s.call_started_at is not None and s.event_at is not None
    assert s.call_started_at < s.event_at


def test_hora_do_pbx_vira_utc_naive() -> None:
    valor = parse_pbx_datetime("2026-08-19 13:51:01.256211")
    assert isinstance(valor, datetime)
    assert valor.tzinfo is None
    assert parse_pbx_datetime("2026-08-19 13:51:01") is not None
    assert parse_pbx_datetime("") is None
    assert parse_pbx_datetime(None) is None
    assert parse_pbx_datetime("ontem") is None


def test_ramal_do_topico_e_reserva_para_payload_desconhecido() -> None:
    assert ramal_from_topic("v1/data/extenStatus/0119") == "0119"
    assert ramal_from_topic("v1/data/cdr") is None
    assert ramal_from_topic("") is None
