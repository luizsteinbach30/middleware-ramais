"""Testes do fallback de credenciais no envio de config (requisito: telefone
cuja senha já foi trocada para a 'nova')."""

from __future__ import annotations

import pytest

from middleware_monitor.domain.extension_configurator.apply import (
    _send_config_with_fallback,
    build_creds_chain,
)
from middleware_monitor.integrations.extension_configurator.vendors import (
    VendorAuthError,
    VendorCredentials,
)


class _FakeAdapter:
    """Aceita exatamente uma credencial; recusa as demais com a exceção dada."""

    def __init__(self, accept: tuple[str, str], reject_factory=None) -> None:
        self.accept = accept
        self.reject_factory = reject_factory or (
            lambda: VendorAuthError("login recusado")
        )
        self.attempts: list[tuple[str, str]] = []

    async def send_config(
        self, ip: str, creds: VendorCredentials, cfg: bytes, *, fmt: str = "xml",
    ) -> None:
        self.attempts.append((creds.username, creds.password))
        if (creds.username, creds.password) != self.accept:
            raise self.reject_factory()


# --- build_creds_chain -------------------------------------------------------


def test_chain_default_so_atual() -> None:
    chain = build_creds_chain({"web_user": "admin", "web_password": "admin"})
    assert len(chain) == 1
    assert (chain[0].username, chain[0].password) == ("admin", "admin")


def test_chain_inclui_nova_quando_configurada() -> None:
    chain = build_creds_chain({
        "web_user": "admin", "web_password": "admin",
        "nova_web_user": "admin", "nova_web_password": "S3nh@Nova",
    })
    assert len(chain) == 2
    assert (chain[1].username, chain[1].password) == ("admin", "S3nh@Nova")


def test_chain_nao_duplica_quando_nova_igual_atual() -> None:
    chain = build_creds_chain({
        "web_user": "admin", "web_password": "admin",
        "nova_web_user": "admin", "nova_web_password": "admin",
    })
    assert len(chain) == 1


def test_chain_nova_so_senha_herda_user() -> None:
    chain = build_creds_chain({
        "web_user": "operador", "web_password": "antiga",
        "nova_web_password": "nova",
    })
    assert (chain[1].username, chain[1].password) == ("operador", "nova")


# --- _send_config_with_fallback ----------------------------------------------


@pytest.mark.asyncio
async def test_fallback_tenta_nova_apos_auth_error() -> None:
    adapter = _FakeAdapter(accept=("admin", "S3nh@Nova"))
    chain = [
        VendorCredentials("admin", "admin"),
        VendorCredentials("admin", "S3nh@Nova"),
    ]
    await _send_config_with_fallback(adapter, "10.0.0.5", chain, b"<cfg/>")
    assert adapter.attempts == [("admin", "admin"), ("admin", "S3nh@Nova")]


@pytest.mark.asyncio
async def test_fallback_nao_tenta_segunda_quando_primeira_ok() -> None:
    adapter = _FakeAdapter(accept=("admin", "admin"))
    chain = [
        VendorCredentials("admin", "admin"),
        VendorCredentials("admin", "S3nh@Nova"),
    ]
    await _send_config_with_fallback(adapter, "10.0.0.5", chain, b"<cfg/>")
    assert adapter.attempts == [("admin", "admin")]


@pytest.mark.asyncio
async def test_fallback_propaga_auth_error_quando_todas_falham() -> None:
    adapter = _FakeAdapter(accept=("ninguem", "ninguem"))
    chain = [
        VendorCredentials("admin", "admin"),
        VendorCredentials("admin", "outra"),
    ]
    with pytest.raises(VendorAuthError):
        await _send_config_with_fallback(adapter, "10.0.0.5", chain, b"<cfg/>")
    assert len(adapter.attempts) == 2  # tentou as duas


@pytest.mark.asyncio
async def test_fallback_aborta_em_erro_nao_auth() -> None:
    """Erro que não é de autenticação (rede, 5xx) não deve disparar tentativa
    com a credencial seguinte."""
    adapter = _FakeAdapter(
        accept=("admin", "admin"),
        reject_factory=lambda: RuntimeError("falha de rede"),
    )
    chain = [
        VendorCredentials("errada", "errada"),
        VendorCredentials("admin", "admin"),
    ]
    with pytest.raises(RuntimeError) as exc:
        await _send_config_with_fallback(adapter, "10.0.0.5", chain, b"<cfg/>")
    assert not isinstance(exc.value, VendorAuthError)
    assert adapter.attempts == [("errada", "errada")]  # parou na primeira
