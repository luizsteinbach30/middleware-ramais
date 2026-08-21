"""Ciclo de vida da conexão com o broker MQTT.

O que se protege aqui é o que o broker vê do outro lado: encerrar sem DISCONNECT
limpo deixa a sessão durável pendurada no EMQX guardando fila, e reconexão em
ciclo costuma ser dois coletores brigando pelo mesmo identificador.
"""

from __future__ import annotations

from middleware_monitor.integrations.mqtt_client import (
    FLAP_LIMITE,
    MqttConnection,
    MqttEndpoint,
)


def test_stop_espera_o_disconnect_antes_de_matar_a_thread(monkeypatch) -> None:
    """A causa das "sessões fechadas" penduradas no EMQX.

    `disconnect()` só enfileira o pacote — quem escreve no socket é a thread de
    rede. Chamar `loop_stop()` na sequência matava a thread antes do DISCONNECT
    sair, e o broker via a conexão cair de forma suja. Com `clean_session=False`
    o EMQX então mantém a sessão viva esperando o cliente voltar, guardando fila.
    """
    ordem: list[str] = []

    class ClienteFalso:
        def disconnect(self) -> None:
            ordem.append("disconnect")

        def loop_stop(self) -> None:
            ordem.append("loop_stop")

    conn = MqttConnection(
        endpoint=MqttEndpoint(host="h", port=1883),
        client_id="mwmonitor-teste",
        topics=["v1/#"],
    )
    conn._client = ClienteFalso()  # type: ignore[assignment]
    # Simula o broker confirmando a desconexão logo depois do pedido.
    conn._disconnected.set()

    conn.stop()
    assert ordem == ["disconnect", "loop_stop"]


def test_stop_nao_trava_quando_o_broker_nao_confirma(monkeypatch) -> None:
    """Broker mudo não pode segurar a parada do serviço.

    A espera tem teto: o pior caso é a sessão ficar pendurada, que é bem melhor
    do que travar o encerramento (ou a tela de configuração) esperando um broker
    que não responde.
    """
    import middleware_monitor.integrations.mqtt_client as mod

    monkeypatch.setattr(mod, "DISCONNECT_TIMEOUT", 0.05)

    class ClienteMudo:
        def disconnect(self) -> None:
            pass  # nunca confirma

        def loop_stop(self) -> None:
            pass

    conn = MqttConnection(
        endpoint=MqttEndpoint(host="h", port=1883),
        client_id="mwmonitor-teste",
        topics=["v1/#"],
    )
    conn._client = ClienteMudo()  # type: ignore[assignment]
    conn.stop()  # não pode levantar nem pendurar
    assert conn.state == "disconnected"


def test_reconexao_em_ciclo_e_denunciada() -> None:
    """Duas instâncias com o mesmo `client_id` se derrubam em revezamento.

    Em MQTT 3.1.1 o broker não diz por que derrubou — só fecha o socket. Então
    a única forma de distinguir "rede ruim" de "identificador duplicado" é a
    frequência, e o operador precisa ver isso escrito.
    """
    conn = MqttConnection(
        endpoint=MqttEndpoint(host="h", port=1883),
        client_id="mwmonitor-duplicado",
        topics=["v1/#"],
    )
    for _ in range(FLAP_LIMITE - 1):
        assert conn._registrar_queda_e_detectar_flap() is False
    assert conn._registrar_queda_e_detectar_flap() is True
