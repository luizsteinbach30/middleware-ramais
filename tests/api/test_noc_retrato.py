"""Retrato para o NOC — ``docs/AGENTE-NOC.md`` itens 11 e 12 (etapa I3 do NOC).

O que se prova aqui:

- toda chave da config padrão tem um lado decidido (valor, só "definida" ou não sai);
- nenhum segredo sai — nem a senha SIP, nem as senhas web, nem o usuário de autenticação;
- o retrato atravessa o filtro de segredos do NOC **sem perder nada**;
- as seções seguem o catálogo do fabricante;
- o coletor diz se estava ouvindo, e hora sem cobertura não vira zero.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from middleware_monitor.core.models import Device, MqttConnectionEvent, MqttMessage
from middleware_monitor.domain.extension_configurator import repository as repo
from middleware_monitor.domain.extension_configurator.defaults import CHAVES_SECRETAS, default_config_padrao
from middleware_monitor.domain.mqtt import repository as mqtt_repo
from middleware_monitor.domain.noc import retrato, telemetria

# A mesma expressão de ``noc-workconnect/api/src/dominio/telemetria/telemetria.ts``:
# o NOC tira do lote, em qualquer profundidade, toda chave com nome de segredo.
NOME_DE_SEGREDO_NO_NOC = re.compile(
    r"senha|passw|secret|segredo|token|credencial|pwd|pin$|auth", re.IGNORECASE
)


def _filtro_do_noc(valor: Any) -> Any:
    if isinstance(valor, list):
        return [_filtro_do_noc(v) for v in valor]
    if isinstance(valor, dict):
        return {k: _filtro_do_noc(v) for k, v in valor.items() if not NOME_DE_SEGREDO_NO_NOC.search(k)}
    return valor


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _lote(db) -> dict[str, Any]:
    lote, _, _ = telemetria.montar_lote(db, telemetria.cursores(db))
    return lote


def _ambiente_com_segredos(db, *, nome: str = "LJ 22 Sul - Caixas", modelo: str = "Intelbras TIP 125i"):
    env = repo.create_environment(db, nome=nome, modelo_telefone=modelo)
    repo.update_environment(
        db,
        env,
        config_padrao={
            "web_user": "admin-da-loja",
            "web_password": "SENHA-WEB-QUE-NAO-SAI",
            "nova_web_password": "NOVA-SENHA-QUE-NAO-SAI",
            "menu_password": "MENU-QUE-NAO-SAI",
            "keylock_password": "TECLADO-QUE-NAO-SAI",
            "sip_server": "PBX-QUE-NAO-SAI.local",
            "hotline_enable": 1,
            "hotline_number": "9000",
            "register_expiration": 60,
        },
    )
    agora = _agora()
    dev = Device(
        name="2211", ip="10.51.22.11", logical_status="available", created_at=agora, updated_at=agora
    )
    db.add(dev)
    db.flush()
    repo.save_lines(
        db,
        env,
        [
            repo.new_line(
                ip="10.51.22.11",
                numero_ramal="2211",
                user_auth="AUTH-QUE-NAO-SAI",
                senha_sip="SIP-QUE-NAO-SAI",
                servidor_sip="SERVIDOR-QUE-NAO-SAI",
                nome_visivel="Caixa 1",
                numero_abreviado="11",
            ),
            repo.new_line(
                ip="", numero_ramal="2212", senha_sip="OUTRA-SIP-QUE-NAO-SAI", nome_visivel="Caixa 2"
            ),
        ],
    )
    db.commit()
    return env


def test_toda_chave_da_config_padrao_tem_um_lado_decidido() -> None:
    listas = (retrato.CONFIG_COM_VALOR, retrato.CONFIG_SO_DEFINIDA, retrato.CONFIG_NAO_SAI)
    todas = [c for lista in listas for c in lista]
    assert len(todas) == len(set(todas)), "uma chave não pode estar em duas listas"
    # Chave nova em defaults.py sem lado decidido quebra aqui — de propósito.
    assert set(default_config_padrao()) == set(todas)
    assert set(CHAVES_SECRETAS) <= set(retrato.CONFIG_SO_DEFINIDA)


def test_o_retrato_dos_ambientes_nao_leva_segredo_nenhum(db) -> None:
    env = _ambiente_com_segredos(db)
    lote = _lote(db)
    texto = json.dumps(lote, ensure_ascii=False)
    for segredo in (
        "SENHA-WEB-QUE-NAO-SAI",
        "NOVA-SENHA-QUE-NAO-SAI",
        "MENU-QUE-NAO-SAI",
        "TECLADO-QUE-NAO-SAI",
        "SIP-QUE-NAO-SAI",
        "AUTH-QUE-NAO-SAI",
        "SERVIDOR-QUE-NAO-SAI",
        "PBX-QUE-NAO-SAI",
        "admin-da-loja",
    ):
        assert segredo not in texto, segredo

    [amb] = lote["ambientes"]
    assert amb["id"] == env.id
    assert amb["nome"] == "LJ 22 Sul - Caixas"
    assert amb["ramais"] == 2 and amb["vinculados"] == 1

    campos = {c["chave"]: c for c in amb["configPadrao"]}
    assert set(campos) == set(retrato.CONFIG_COM_VALOR) | set(retrato.CONFIG_SO_DEFINIDA)
    assert campos["register_expiration"] == {"chave": "register_expiration", "valor": 60}
    assert campos["hotline_number"] == {"chave": "hotline_number", "valor": "9000"}
    for chave in retrato.CONFIG_SO_DEFINIDA:
        assert set(campos[chave]) == {"chave", "definida"}, chave
    assert campos["web_password"]["definida"] is True
    assert campos["nova_web_user"]["definida"] is False

    linha = amb["linhas"][0]
    assert set(linha) == {
        "posicao",
        "ramal",
        "nomeVisivel",
        "numeroAbreviado",
        "ip",
        "deviceId",
        "dispositivo",
        "status",
        "ultimoModelo",
        "ultimoMac",
        "ultimaAplicacao",
        "ultimoErro",
    }
    assert (linha["ramal"], linha["nomeVisivel"], linha["numeroAbreviado"], linha["dispositivo"]) == (
        "2211",
        "Caixa 1",
        "11",
        "2211",
    )
    # Nunca aplicado, mas o aparelho está registrado no PBX: é o mesmo "registrado" da planilha daqui.
    assert linha["status"] == "registered"
    assert amb["linhas"][1]["status"] == "pending"
    assert amb["contagemPorStatus"] == {"registered": 1, "pending": 1}
    assert amb["situacao"] == "pendentes"


def test_o_retrato_atravessa_o_filtro_de_segredos_do_noc_sem_perder_nada(db) -> None:
    _ambiente_com_segredos(db)
    lote = _lote(db)
    retrato_do_noc = {k: lote[k] for k in ("ambientes", "coletor", "conexoesMqtt", "mensagensPorHora")}
    # Se o formato usasse o nome da senha como chave, o NOC jogaria fora a
    # informação "definida" junto com a chave, e a tela diria que não há senha.
    assert _filtro_do_noc(retrato_do_noc) == retrato_do_noc


def test_perfis_levam_o_id_do_ambiente(db) -> None:
    env = _ambiente_com_segredos(db)
    perfis = _lote(db)["perfis"]
    assert {p["ambienteId"] for p in perfis} == {env.id}


def test_as_secoes_seguem_o_catalogo_do_fabricante() -> None:
    tip = retrato.secoes_do_modelo("Intelbras TIP 125i")
    assert "avancadas" in tip and "hotline" in tip and "teclas" not in tip
    htek = retrato.secoes_do_modelo("HTEK UC924")
    assert "teclas" in htek and "avancadas" not in htek and "hotline" not in htek
    v5501 = retrato.secoes_do_modelo("Intelbras V5501")
    assert "avancadas" in v5501 and "teclas" in v5501
    assert tip[:4] == ["sip", "hora", "credenciais", "validacao"]


def test_coletor_sem_broker_diz_sem_broker_e_nao_lista_vazia(db) -> None:
    [unico] = retrato.coletor(db)
    assert unico["estado"] == "sem_broker"
    assert unico["broker"] is None


def _broker(db, **extra):
    b = mqtt_repo.create_broker(
        db,
        nome="broker-norte",
        address_input="mqtts://usuario:SENHA-DO-BROKER@10.31.0.5:8883",
        host="10.31.0.5",
        port=8883,
        tls=True,
        username="usuario-do-broker",
        password_plain="SENHA-DO-BROKER",
        topics=["uscall/#"],
        **extra,
    )
    db.commit()
    return b


def test_coletor_estado_vem_do_ao_vivo_ou_do_ultimo_evento(db) -> None:
    b = _broker(db)
    agora = _agora()
    db.add_all(
        [
            MqttConnectionEvent(broker_id=b.id, timestamp=agora - timedelta(hours=2), state="subscribed"),
            MqttConnectionEvent(
                broker_id=b.id,
                timestamp=agora - timedelta(minutes=70),
                state="disconnected",
                detail="conexão perdida",
            ),
            MqttMessage(
                broker_id=b.id, received_at=agora - timedelta(hours=1, minutes=30), topic="t", payload="{}"
            ),
            MqttMessage(broker_id=b.id, received_at=agora - timedelta(hours=30), topic="t", payload="{}"),
        ]
    )
    db.commit()

    [gravado] = retrato.coletor(db)
    assert gravado["estado"] == "desconectado"
    assert gravado["detalhe"] == "conexão perdida"
    assert gravado["mensagens24h"] == 1
    assert gravado["endereco"] == "10.31.0.5:8883"
    assert "SENHA-DO-BROKER" not in json.dumps(gravado) and "usuario-do-broker" not in json.dumps(gravado)

    ao_vivo = {
        "brokers": [
            {
                "broker_id": b.id,
                "state": "subscribed",
                "detail": "assinado: uscall/#",
                "connected_since": agora,
            }
        ]
    }
    [vivo] = retrato.coletor(db, ao_vivo)
    assert vivo["estado"] == "conectado"
    assert vivo["desde"] == agora.isoformat(timespec="seconds")


def test_broker_desligado_nao_entra_no_coletor(db) -> None:
    _broker(db, enabled=False)
    assert [c["estado"] for c in retrato.coletor(db)] == ["sem_broker"]


def test_mensagens_por_hora_so_das_horas_em_que_o_coletor_ouviu(db) -> None:
    b = _broker(db)
    agora = datetime(2026, 9, 17, 14, 45, 0)

    def h(horas: int, minutos: int = 0) -> datetime:
        """``horas`` antes das 14:00, mais ``minutos``."""
        return datetime(2026, 9, 17, 14, 0, 0) - timedelta(hours=horas) + timedelta(minutes=minutos)

    db.add_all(
        [
            # 11:10 conectou, 12:30 caiu — antes disso não há histórico nenhum.
            MqttConnectionEvent(broker_id=None, timestamp=h(3, 5), state="startup"),
            MqttConnectionEvent(broker_id=b.id, timestamp=h(3, 10), state="subscribed"),
            MqttConnectionEvent(broker_id=b.id, timestamp=h(2, 30), state="disconnected"),
            MqttMessage(broker_id=b.id, received_at=h(3, 20), topic="t", payload="{}"),
            MqttMessage(broker_id=b.id, received_at=h(3, 40), topic="t", payload="{}"),
            # Mensagem numa hora sem cobertura (13:10) não transforma a hora em "ouvida".
            MqttMessage(broker_id=b.id, received_at=h(1, 10), topic="t", payload="{}"),
        ]
    )
    db.commit()

    horas = retrato.mensagens_por_hora(db, agora=agora)
    assert [(x["hora"], x["mensagens"], x["coberturaPct"]) for x in horas] == [
        ("2026-09-17T11:00:00", 2, 83.33),
        ("2026-09-17T12:00:00", 0, 50.0),
    ]
    # 12:00 foi ouvida pela metade e ninguém publicou: vai como zero, porque é silêncio medido.
    # 13:00 e 14:00 não foram ouvidas: não vão, nem como zero.


def test_conexoes_andam_por_cursor_e_comecam_24h_para_tras(db) -> None:
    b = _broker(db)
    agora = _agora()
    velho = MqttConnectionEvent(broker_id=b.id, timestamp=agora - timedelta(days=3), state="subscribed")
    db.add(velho)
    db.flush()
    db.add(
        MqttConnectionEvent(
            broker_id=b.id, timestamp=agora - timedelta(hours=1), state="error", detail="recusado"
        )
    )
    db.commit()

    cur = telemetria.cursores(db)
    lote, novos, _ = telemetria.montar_lote(db, cur)
    assert [(c["estado"], c["detalhe"]) for c in lote["conexoesMqtt"]] == [("error", "recusado")]

    cur_seguinte = {**cur, **novos}
    lote2, novos2, _ = telemetria.montar_lote(db, cur_seguinte)
    assert lote2["conexoesMqtt"] == []
    assert novos2["conexoes"] == novos["conexoes"]
