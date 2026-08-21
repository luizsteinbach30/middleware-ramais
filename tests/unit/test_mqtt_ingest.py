"""Coletor: montagem da linha do ledger e comportamento sob pressão.

O que estes testes protegem é a promessa central: a mensagem é gravada como
chegou, e nada é descartado em silêncio.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from middleware_monitor.domain.mqtt import service
from middleware_monitor.domain.mqtt.service import MqttIngestor, _build_row

AGORA = datetime(2026, 8, 19, 17, 0, 0, tzinfo=UTC).replace(tzinfo=None)
PAYLOAD = (
    '{"retorno": {"0119": {"status": "Ocupado", "ramal": "0119", '
    '"data": "2026-08-19 13:51:01.256211", "numero": "800"}}}'
)


def test_linha_guarda_o_payload_como_chegou() -> None:
    row, statuses = _build_row(
        1, "v1/data/extenStatus/0119", PAYLOAD.encode(), 1, False, 0, AGORA,
    )
    assert row["payload"] == PAYLOAD  # verbatim: é o que serve de prova
    assert row["payload_bytes"] == len(PAYLOAD.encode())
    assert row["b64"] is False and row["truncated"] is False
    assert row["ramal"] == "0119"
    assert row["event_at"] is not None
    assert row["qos"] == 1 and row["pinned"] is False
    # O parse viaja junto com a linha: a normalização não redecodifica o JSON.
    assert [st.ramal for st in statuses] == ["0119"]


def test_payload_binario_vira_base64_marcado() -> None:
    bruto = b"\x00\x01\x02\xff"
    row, statuses = _build_row(1, "v1/bin", bruto, 0, False, 0, AGORA)
    assert statuses == []
    assert row["b64"] is True
    assert base64.b64decode(row["payload"]) == bruto
    assert row["payload_bytes"] == 4


def test_truncamento_marca_a_linha_e_preserva_o_tamanho_original() -> None:
    grande = b"x" * 3000
    row, _ = _build_row(1, "v1/grande", grande, 0, False, 1, AGORA)  # 1 KB
    assert row["truncated"] is True
    assert len(row["payload"]) == 1024
    assert row["payload_bytes"] == 3000  # o tamanho real fica registrado


def test_ramal_vem_do_topico_quando_o_payload_nao_e_reconhecido() -> None:
    row, _ = _build_row(1, "v1/data/extenStatus/0307", b"qualquer coisa", 0, False, 0, AGORA)
    assert row["ramal"] == "0307"
    assert row["event_at"] is None
    row, _ = _build_row(1, "v1/data/cdr", b"qualquer coisa", 0, False, 0, AGORA)
    assert row["ramal"] is None


def test_fila_cheia_descarta_a_mais_antiga_e_conta(monkeypatch: pytest.MonkeyPatch) -> None:
    # Descarte silencioso seria pior que não registrar: o contador aparece na tela.
    monkeypatch.setattr(service, "QUEUE_MAX", 3)
    ing = MqttIngestor(db_factory=lambda: pytest.fail("não deveria gravar aqui"))
    handler = ing._make_on_message(1, 0)
    for i in range(5):
        handler(f"v1/t/{i}", b"m", 0, False)
    assert ing.received == 5
    assert ing.dropped == 2
    assert len(ing._buffer) == 3
    assert [row["topic"] for row, _ in ing._buffer] == ["v1/t/2", "v1/t/3", "v1/t/4"]


def test_falha_de_gravacao_devolve_o_lote_para_a_fila() -> None:
    class BancoQuebrado:
        def __enter__(self) -> None:
            raise RuntimeError("disco cheio")

        def __exit__(self, *_a: object) -> None:  # pragma: no cover
            return None

    ing = MqttIngestor(db_factory=BancoQuebrado)  # type: ignore[arg-type]
    handler = ing._make_on_message(1, 0)
    handler("v1/t/1", b"m", 0, False)
    handler("v1/t/2", b"m", 0, False)

    asyncio.run(ing._flush())

    # Comprovante não se joga fora porque o banco piscou.
    assert len(ing._buffer) == 2
    assert ing.persisted == 0
    assert ing.persist_failures == 1


def _payload_agora(segundos_atras: float = 5.0) -> bytes:
    """Payload no formato real, com a hora local do PBX N segundos atrás."""
    momento = datetime.now() - timedelta(seconds=segundos_atras)
    corpo = {
        "retorno": {
            "0119": {
                "status": "Ocupado",
                "ramal": "0119",
                "data": momento.strftime("%Y-%m-%d %H:%M:%S.%f"),
                "numero": "800",
            }
        }
    }
    return json.dumps(corpo).encode()


def test_estado_reporta_contadores_para_a_tela() -> None:
    ing = MqttIngestor(db_factory=lambda: pytest.fail("sem banco neste teste"))
    handler = ing._make_on_message(1, 0)
    handler("v1/data/extenStatus/0119", _payload_agora(5), 1, False)
    s = ing.status()
    assert s["received"] == 1
    assert s["queue_depth"] == 1
    assert s["per_minute"] >= 1
    assert s["last_message_at"] is not None
    # Atraso entre o evento no PBX e o recebimento — é o que denuncia fila
    # acumulada no broker.
    assert s["avg_lag_seconds"] is not None
    assert 3 <= s["avg_lag_seconds"] <= 10
    assert s["clock_outliers"] == 0


def test_relogio_muito_fora_nao_envenena_a_media_mas_e_contado() -> None:
    ing = MqttIngestor(db_factory=lambda: pytest.fail("sem banco neste teste"))
    handler = ing._make_on_message(1, 0)
    handler("v1/data/extenStatus/0119", _payload_agora(5), 1, False)
    handler("v1/data/extenStatus/0119", _payload_agora(9999), 1, False)
    s = ing.status()
    assert s["clock_outliers"] == 1
    assert s["avg_lag_seconds"] is not None and s["avg_lag_seconds"] < 60


def test_pedido_de_reconexao_sem_coletor_rodando_e_inofensivo() -> None:
    # A tela salva o broker mesmo com o coletor parado (ex.: testes de API).
    MqttIngestor().request_reload()


def test_payload_grande_ainda_e_reconhecido_antes_do_corte() -> None:
    """Truncar é decisão de disco, não de leitura.

    Se o corte viesse antes do parse, o JSON quebraria no meio e a mensagem
    perderia ramal e hora do evento — justamente as grandes, que são as que
    mais interessam quando se procura um comprovante.
    """
    recheio = "z" * 4000
    corpo = json.dumps(
        {"retorno": {"0119": {"status": "Ocupado", "ramal": "0119",
                              "data": "2026-08-19 13:51:01.256211",
                              "numero": "800", "obs": recheio}}}
    ).encode()
    row, statuses = _build_row(1, "v1/data/extenStatus/0119", corpo, 0, False, 1, AGORA)
    assert row["truncated"] is True
    assert row["ramal"] == "0119" and row["event_at"] is not None
    assert [st.status for st in statuses] == ["Ocupado"]


def test_broker_sem_client_id_ganha_um_e_persiste(db) -> None:
    """Sessão durável exige identificador estável.

    O `client_id` era gerado na hora da conexão e descartado, então **cada**
    conexão entrava no broker como um cliente diferente. Com
    `clean_session=False` isso é caro: o EMQX guarda uma sessão nova (com fila
    de mensagens) para cada identificador, e ela fica pendurada para sempre —
    o operador vê sessões fechadas se acumulando no broker.
    """
    from middleware_monitor.core.db import session_factory
    from middleware_monitor.core.models import MqttBroker
    from middleware_monitor.domain.mqtt import repository as repo

    broker = repo.create_broker(
        db, nome="EMQX", address_input="h", host="h", port=1883,
        password_plain="", topics=["v1/#"],
    )
    broker.client_id = ""  # simula linha antiga/importada sem identificador
    broker_id = broker.id
    db.commit()
    db.close()

    def ler_client_id() -> str:
        with session_factory() as s:
            return s.get(MqttBroker, broker_id).client_id  # type: ignore[union-attr]

    ing = MqttIngestor(db_factory=session_factory)
    asyncio.run(ing._connect_all())
    primeiro = ler_client_id()
    assert primeiro, "o identificador tinha de ter sido gravado"

    asyncio.run(ing._connect_all())
    assert ler_client_id() == primeiro, "não pode mudar a cada conexão"
