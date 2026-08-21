"""Verificação de update: a cadeia de certificados vem do certifi.

Este é o teste do incidente relatado em 2026-08-21: a máquina do cliente logava
`CERTIFICATE_VERIFY_FAILED — unable to get local issuer certificate` em **toda**
verificação de update, e portanto nunca descobria que havia versão nova. Sem
contexto TLS explícito, o Python cai no armazenamento de certificados do Windows,
onde costuma faltar o emissor intermediário da cadeia da API do GitHub.

Só o updater falhava porque só ele usa `urlopen`; o resto do app fala HTTPS por
`httpx`, que já usa o certifi. O `cacert.pem` sempre esteve dentro do `.exe`.

Rodando do fonte o erro não aparece (o venv tem a cadeia completa), então o que
dá para afirmar em teste é o que de fato faltava: **o parâmetro `context`**.
"""

from __future__ import annotations

import ssl
from typing import Any

import pytest

from middleware_monitor.desktop import UpdateChecker, _contexto_tls


class _RespostaFalsa:
    def __init__(self, corpo: bytes) -> None:
        self._corpo = corpo

    def read(self) -> bytes:
        return self._corpo

    def __enter__(self) -> _RespostaFalsa:
        return self

    def __exit__(self, *_a: object) -> None:
        return None


def test_verificacao_de_update_usa_contexto_do_certifi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capturado: dict[str, Any] = {}

    def falso_urlopen(_req: Any, **kwargs: Any) -> _RespostaFalsa:
        capturado.update(kwargs)
        return _RespostaFalsa(b"[]")

    monkeypatch.setattr("urllib.request.urlopen", falso_urlopen)

    UpdateChecker(current_version="2.8.0", repo="dono/repo").check()

    assert "context" in capturado, (
        "sem `context` o Python usa o armazenamento do SO — foi exatamente "
        "isso que quebrou a verificação de update em campo"
    )
    assert isinstance(capturado["context"], ssl.SSLContext)
    # Verificação de certificado continua LIGADA: o objetivo é usar a cadeia
    # certa, não desligar a checagem.
    assert capturado["context"].verify_mode == ssl.CERT_REQUIRED
    assert capturado["context"].check_hostname is True


def test_contexto_carrega_a_cadeia_do_certifi() -> None:
    ctx = _contexto_tls()
    assert ctx is not None
    # `get_ca_certs()` vazio significaria contexto sem âncora — verificaria
    # contra nada e voltaríamos ao erro original.
    assert ctx.get_ca_certs(), "o contexto tem de trazer os CAs do certifi"


def test_sem_certifi_cai_no_contexto_padrao_em_vez_de_derrubar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Updater que não verifica é ruim; updater que derruba o app é pior.

    O `check()` roda em thread de fundo dentro do executável: uma exceção aqui
    não tem quem a trate e mata a janela do usuário.
    """
    import builtins

    real_import = builtins.__import__

    def sem_certifi(nome: str, *args: Any, **kwargs: Any) -> Any:
        if nome == "certifi":
            raise ImportError("simulando instalação sem certifi")
        return real_import(nome, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", sem_certifi)
    assert _contexto_tls() is None  # contexto padrão, sem levantar


def test_falha_de_rede_continua_devolvendo_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # O contrato de `check()` não muda: erro de rede vira None + log, nunca
    # exceção — é o que mantém o app de pé quando não há internet.
    def explode(_req: Any, **_kwargs: Any) -> None:
        raise OSError("sem rede")

    monkeypatch.setattr("urllib.request.urlopen", explode)
    assert UpdateChecker(current_version="2.8.0", repo="dono/repo").check() is None
