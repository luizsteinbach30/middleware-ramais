"""O canal que o NOC anuncia no heartbeat (27/09): aceito na forma de sempre, nunca para baixo."""

from __future__ import annotations

import pytest

from middleware_monitor.domain.noc import cliente


def test_troca_de_canal_em_https_e_aceita() -> None:
    assert (
        cliente.aceitar_canal_anunciado(
            "https://agente.noc.workconnect.com.br:8446", "https://agente2.noc.workconnect.com.br:8446/"
        )
        == "https://agente2.noc.workconnect.com.br:8446"
    )
    # Sem esquema vira https, como no enrolamento.
    assert (
        cliente.aceitar_canal_anunciado("https://a.exemplo:8446", "b.exemplo:8446")
        == "https://b.exemplo:8446"
    )


def test_rebaixamento_para_http_e_recusado() -> None:
    with pytest.raises(ValueError, match="rebaixamento recusado"):
        cliente.aceitar_canal_anunciado(
            "https://agente.noc.workconnect.com.br:8446", "http://agente.noc.workconnect.com.br:8446"
        )


def test_o_laboratorio_em_http_continua_como_esta() -> None:
    # Enrolado em http (homologação local): http continua valendo, e subir para https também.
    assert (
        cliente.aceitar_canal_anunciado("http://localhost:8086", "http://localhost:8446")
        == "http://localhost:8446"
    )
    assert (
        cliente.aceitar_canal_anunciado("http://localhost:8086", "https://localhost:8446")
        == "https://localhost:8446"
    )


@pytest.mark.parametrize("anunciado", ["", "   ", None, 12, "ftp://x"])
def test_forma_invalida_e_recusada(anunciado: object) -> None:
    with pytest.raises(ValueError):
        cliente.aceitar_canal_anunciado("https://a.exemplo:8446", anunciado)
