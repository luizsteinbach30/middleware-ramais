"""Sonda de ping do Linux: "host não respondeu" não é "o servidor não consegue pingar".

No host do cliente (2026-09-09) todo aparelho aparecia offline e o Aplicar
parava em "host não responde ao ping". A sonda engolia o stderr do ``ping`` e
devolvia ``None`` para tudo — inclusive quando o próprio ``ping`` recusava
abrir o socket. Estes testes fixam a distinção pelo código de saída do
iputils (1 = sem resposta; 2 ou mais = a sonda falhou) e o aviso único no log.
"""

from __future__ import annotations

import asyncio

import pytest

from middleware_monitor.integrations.network import linux


class _Proc:
    def __init__(self, rc: int, out: bytes = b"", err: bytes = b"") -> None:
        self.returncode = rc
        self._out, self._err = out, err

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._out, self._err


def _fake_exec(proc: _Proc | BaseException):
    async def _exec(*_args, **_kwargs):
        if isinstance(proc, BaseException):
            raise proc
        return proc

    return _exec


@pytest.fixture(autouse=True)
def _reset():
    linux.reset_diagnostico_para_testes()
    yield
    linux.reset_diagnostico_para_testes()


def test_host_respondeu_devolve_latencia(monkeypatch: pytest.MonkeyPatch) -> None:
    out = b"64 bytes from 10.0.0.5: icmp_seq=1 ttl=64 time=12.4 ms\n"
    monkeypatch.setattr(linux.asyncio, "create_subprocess_exec", _fake_exec(_Proc(0, out)))
    probe = linux.LinuxPingProbe()
    assert asyncio.run(probe.ping("10.0.0.5", 1500)) == 12
    assert probe.ultimo_erro is None


def test_host_sem_resposta_nao_e_erro_da_sonda(monkeypatch: pytest.MonkeyPatch) -> None:
    """Código 1 é telefone desligado — offline de verdade, sem culpar o servidor."""
    monkeypatch.setattr(linux.asyncio, "create_subprocess_exec", _fake_exec(_Proc(1)))
    probe = linux.LinuxPingProbe()
    assert asyncio.run(probe.ping("10.0.0.5", 1500)) is None
    assert probe.ultimo_erro is None


def test_ping_sem_permissao_e_denunciado(monkeypatch: pytest.MonkeyPatch) -> None:
    """O caso do sandbox do systemd: o binário existe, mas não abre o socket."""
    err = b"ping: socket: Operation not permitted\n"
    monkeypatch.setattr(linux.asyncio, "create_subprocess_exec", _fake_exec(_Proc(2, b"", err)))
    probe = linux.LinuxPingProbe()
    assert asyncio.run(probe.ping("10.0.0.5", 1500)) is None
    assert probe.ultimo_erro == "ping: socket: Operation not permitted"


def test_ping_ausente_diz_o_pacote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linux.asyncio, "create_subprocess_exec", _fake_exec(FileNotFoundError("ping")))
    probe = linux.LinuxPingProbe()
    assert asyncio.run(probe.ping("10.0.0.5", 1500)) is None
    assert probe.ultimo_erro is not None
    assert "iputils-ping" in probe.ultimo_erro


def test_aviso_no_log_sai_uma_vez_por_processo(monkeypatch: pytest.MonkeyPatch) -> None:
    avisos: list[dict] = []
    monkeypatch.setattr(linux.log, "warning", lambda evento, **kw: avisos.append({"evento": evento, **kw}))
    err = b"ping: connect: Network is unreachable\n"
    monkeypatch.setattr(linux.asyncio, "create_subprocess_exec", _fake_exec(_Proc(2, b"", err)))
    for ip in ("10.0.0.5", "10.0.0.6", "10.0.0.7"):
        assert asyncio.run(linux.LinuxPingProbe().ping(ip, 1500)) is None
    assert len(avisos) == 1
    assert avisos[0]["evento"] == "ping_indisponivel"
    assert "Network is unreachable" in avisos[0]["motivo"]


def test_erro_da_sonda_e_zerado_na_chamada_seguinte(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = linux.LinuxPingProbe()
    monkeypatch.setattr(linux.asyncio, "create_subprocess_exec", _fake_exec(_Proc(2, b"", b"ping: x\n")))
    asyncio.run(probe.ping("10.0.0.5", 1500))
    assert probe.ultimo_erro
    monkeypatch.setattr(linux.asyncio, "create_subprocess_exec", _fake_exec(_Proc(1)))
    asyncio.run(probe.ping("10.0.0.5", 1500))
    assert probe.ultimo_erro is None


def test_apply_diz_que_o_culpado_e_o_servidor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mensagem por linha do Aplicar tem de apontar o servidor, não o telefone."""
    from middleware_monitor.domain.extension_configurator import apply as apply_mod

    class _Probe:
        ultimo_erro = "ping: socket: Operation not permitted"

        async def ping(self, _ip: str, _timeout_ms: int) -> int | None:
            return None

    monkeypatch.setattr(apply_mod, "make_ping_probe", _Probe)
    respondeu, motivo = asyncio.run(apply_mod._ping_host("10.0.0.5"))
    assert respondeu is False
    assert motivo == "ping: socket: Operation not permitted"
