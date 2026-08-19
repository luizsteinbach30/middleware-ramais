"""Interpretação do endereço do broker e casamento de tópicos.

O contrato desta camada é "não adivinhar": o que o operador não informou fica
``None`` e vira candidato a sondar. A porta que ele digitou sempre é testada.
"""

from __future__ import annotations

import pytest

from middleware_monitor.domain.mqtt.address import (
    build_candidates,
    match_topic,
    match_topic_any,
    parse_broker_address,
    topic_tree,
    validate_topic_filter,
)


@pytest.mark.parametrize(
    ("entrada", "host", "port", "transport", "tls"),
    [
        ("emqx.ambimon.com", "emqx.ambimon.com", None, None, None),
        ("emqx.ambimon.com:8883", "emqx.ambimon.com", 8883, None, None),
        ("ssl://emqx.ambimon.com:8883", "emqx.ambimon.com", 8883, "tcp", True),
        ("mqtts://emqx.ambimon.com", "emqx.ambimon.com", None, "tcp", True),
        ("tcp://10.0.0.5:1883", "10.0.0.5", 1883, "tcp", False),
        ("ws://host:8083/mqtt", "host", 8083, "websockets", False),
        ("wss://host/mqtt", "host", None, "websockets", True),
        ("http://host:18083", "host", 18083, "websockets", False),
        ("https://host:8084/mqtt", "host", 8084, "websockets", True),
        ("[::1]:1883", "::1", 1883, None, None),
        ("user:senha@host:1883", "host", 1883, None, None),
    ],
)
def test_parse_reconhece_as_formas_que_o_operador_digita(
    entrada: str, host: str, port: int | None, transport: str | None, tls: bool | None,
) -> None:
    p = parse_broker_address(entrada)
    assert (p.host, p.port, p.transport, p.tls) == (host, port, transport, tls)


@pytest.mark.parametrize("entrada", ["", "   ", "ftp://host", "host:0", "host:abc", "host:99999"])
def test_parse_recusa_entrada_invalida(entrada: str) -> None:
    with pytest.raises(ValueError):
        parse_broker_address(entrada)


def test_porta_digitada_e_testada_nos_quatro_transportes() -> None:
    # Só a rede diz o que uma porta é; o texto não decide por ela.
    cands = build_candidates(parse_broker_address("host:9999"))
    da_porta = [c for c in cands if c.port == 9999]
    assert len(da_porta) == 4
    assert {(c.transport, c.tls) for c in da_porta} == {
        ("tcp", True), ("tcp", False), ("websockets", True), ("websockets", False),
    }


def test_sem_porta_varre_as_padrao_com_o_cifrado_primeiro() -> None:
    cands = build_candidates(parse_broker_address("emqx.ambimon.com"))
    assert [(c.port, c.tls) for c in cands] == [
        (8883, True), (1883, False), (8084, True), (8083, False),
    ]


def test_esquema_sem_porta_usa_a_porta_daquele_esquema_primeiro() -> None:
    cands = build_candidates(parse_broker_address("ws://host"))
    assert (cands[0].port, cands[0].transport, cands[0].tls) == (8083, "websockets", False)


def test_porta_do_painel_web_tambem_sonda_as_padrao() -> None:
    # O engano mais comum: colar a URL do painel do EMQX (18083).
    parsed = parse_broker_address("http://emqx.ambimon.com:18083")
    assert parsed.dashboard_port is True
    portas = [c.port for c in build_candidates(parsed)]
    assert 18083 in portas
    assert 8883 in portas and 1883 in portas


def test_caminho_do_websocket_vai_para_os_candidatos() -> None:
    cands = build_candidates(parse_broker_address("wss://host/caminho"))
    ws = [c for c in cands if c.transport == "websockets"]
    assert ws and all(c.ws_path == "/caminho" for c in ws)
    assert all(c.ws_path is None for c in cands if c.transport == "tcp")


# ── filtros de tópico ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "filtro", ["#", "v1/data/#", "v1/data/extenStatus/+", "v1/+/extenStatus/0119"],
)
def test_filtros_validos(filtro: str) -> None:
    assert validate_topic_filter(filtro) is None


@pytest.mark.parametrize(
    ("filtro", "motivo"),
    [
        ("", "filtro_vazio"),
        ("v1/#/data", "curinga_multinivel_no_fim"),
        ("v1/da#ta", "curinga_multinivel_isolado"),
        ("v1/da+ta", "curinga_um_nivel_isolado"),
    ],
)
def test_filtros_invalidos_dizem_o_motivo(filtro: str, motivo: str) -> None:
    assert validate_topic_filter(filtro) == motivo


def test_casamento_de_topico_segue_o_mqtt() -> None:
    assert match_topic("v1/data/extenStatus/+", "v1/data/extenStatus/0119")
    assert match_topic("v1/data/#", "v1/data/extenStatus/0119")
    assert not match_topic("v1/data/+", "v1/data/extenStatus/0119")
    # Filtro que começa com curinga não alcança tópicos internos do broker...
    assert not match_topic("#", "$SYS/broker/uptime")
    # ...mas na busca sobre o que já está gravado o operador acha o que pediu.
    assert match_topic_any("#", "$SYS/broker/uptime")


def test_arvore_agrupa_ramais_em_um_ramo_so() -> None:
    counts = {f"v1/data/extenStatus/{n}": 3 for n in ("0119", "0307", "1209")}
    counts["v1/data/cdr"] = 1
    ramos = topic_tree(counts)
    assert ramos[0].filter == "v1/data/extenStatus/+"
    assert ramos[0].messages == 9
    assert ramos[0].topics == 3
    assert [r.filter for r in ramos[1:]] == ["v1/data/+"]
