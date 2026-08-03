"""Device actions (v2.7.0): capabilities + execute_action por vendor."""

from __future__ import annotations

from typing import ClassVar

import httpx
import pytest
import respx

from middleware_monitor.integrations.extension_configurator.vendors.base import (
    ACTION_NORMALIZE,
    ActionResult,
    VendorActionUnsupported,
    VendorAdapter,
    VendorCredentials,
)
from middleware_monitor.integrations.extension_configurator.vendors.flyingvoice import (
    FlyingVoiceAdapter,
)
from middleware_monitor.integrations.extension_configurator.vendors.htek import (
    HTEKAdapter,
)
from middleware_monitor.integrations.extension_configurator.vendors.intelbras import (
    IntelbrasAdapter,
)
from middleware_monitor.integrations.extension_configurator.vendors.yealink import (
    YealinkAdapter,
)

_CREDS = VendorCredentials("admin", "admin")


def test_base_default_sem_capabilities() -> None:
    class _Dummy(VendorAdapter):
        vendor_id = "dummy"

        async def fingerprint(self, ip): return 0.0
        async def discover(self, ip, creds): ...
        def generate_config(self, template, row): return b""
        async def send_config(self, ip, creds, cfg, *, fmt="xml"): ...

    assert _Dummy().capabilities() == frozenset()


async def test_base_execute_unsupported_raises() -> None:
    class _Dummy(VendorAdapter):
        vendor_id = "dummy"

        async def fingerprint(self, ip): return 0.0
        async def discover(self, ip, creds): ...
        def generate_config(self, template, row): return b""
        async def send_config(self, ip, creds, cfg, *, fmt="xml"): ...

    with pytest.raises(VendorActionUnsupported):
        await _Dummy().execute_action("1.2.3.4", _CREDS, "normalize", {})


def test_vendors_homologados_declaram_normalize() -> None:
    assert ACTION_NORMALIZE in YealinkAdapter().capabilities()
    assert ACTION_NORMALIZE in FlyingVoiceAdapter().capabilities()
    assert ACTION_NORMALIZE in HTEKAdapter().capabilities()
    assert ACTION_NORMALIZE in IntelbrasAdapter().capabilities()


# --------------------------------------------------------------------- Yealink


@respx.mock
async def test_yealink_normalize_dispara_action_uris() -> None:
    route = respx.get(url__regex=r"https://1\.2\.3\.4/servlet\?key=\w+").mock(
        return_value=httpx.Response(200, text="<html></html>"),
    )
    res = await YealinkAdapter().execute_action("1.2.3.4", _CREDS, "normalize", {})
    assert res.ok
    keys = [c.request.url.params.get("key") for c in route.calls]
    assert "DNDOff" in keys
    assert "MUTE" in keys
    assert keys.count("VOLUME_UP") >= 8   # sobe o volume várias vezes
    # Action URI usa Basic Auth (não a sessão web)
    assert route.calls.last.request.headers.get("Authorization", "").startswith("Basic ")


@respx.mock
async def test_yealink_normalize_401_vira_auth_error() -> None:
    from middleware_monitor.integrations.extension_configurator.vendors.base import (
        VendorAuthError,
    )

    respx.get(url__regex=r"https://1\.2\.3\.4/servlet.*").mock(
        return_value=httpx.Response(401),
    )
    with pytest.raises(VendorAuthError):
        await YealinkAdapter().execute_action("1.2.3.4", _CREDS, "normalize", {})


async def test_yealink_execute_rejeita_acao_desconhecida() -> None:
    with pytest.raises(VendorActionUnsupported):
        await YealinkAdapter().execute_action("1.2.3.4", _CREDS, "set_ip", {})


# ------------------------------------------------------------------------ HTEK


@respx.mock
async def test_htek_normalize_envia_pcode_volume() -> None:
    respx.get("http://1.2.3.4/").mock(
        return_value=httpx.Response(401, headers={"WWW-Authenticate": "Basic"}),
    )
    upload = respx.post("http://1.2.3.4/HLCFG_XML_configuration.htm").mock(
        return_value=httpx.Response(200, text="ok"),
    )
    res = await HTEKAdapter().execute_action("1.2.3.4", _CREDS, "normalize", {})
    assert res.ok and res.rebooted
    body = upload.calls.last.request.content
    assert b"P8503" in body and b"14" in body
    # DND off (P1305 = DND_Enable, localizado no export /download_xml_cfg)
    assert b'<P1305 para="DND_Enable">0</P1305>' in body


# ------------------------------------------------------------------ FlyingVoice


async def test_flyingvoice_normalize_replay_sobrescreve_dnd_e_volume(monkeypatch) -> None:
    ad = FlyingVoiceAdapter()

    class _FakeCli:
        cookies: ClassVar[dict[str, str]] = {"ASPSSIONID": "abc"}

        async def get(self, url):
            html = (
                '<form name="preference">'
                '<input name="CheckString" value="cs123">'
                '<input name="DBID_DND_ENABLE" value="1">'
                '<input name="DBID_HF_OUT_VOL" value="3">'
                '<input name="DBID_SIP_PHONE_NUM" value="8125">'
                '</form>'
            )
            return httpx.Response(200, text=html)

        async def aclose(self): ...

    async def _fake_login(ip, creds):
        return _FakeCli(), "abc"

    captured: dict[str, str] = {}

    def _fake_http10(ip, path, body, cookie, referer):
        captured["path"] = path
        captured["body"] = body
        return 302

    monkeypatch.setattr(ad, "_login", _fake_login)
    monkeypatch.setattr(ad, "_http10_post", _fake_http10)

    res = await ad.execute_action("1.2.3.4", _CREDS, "normalize", {})
    assert res.ok
    assert captured["path"] == "/goform/setSip"
    body = captured["body"]
    assert "DBID_DND_ENABLE=0" in body           # DND off
    assert "DBID_HF_OUT_VOL=9" in body           # volume máx
    assert "DBID_SIP_PHONE_NUM=8125" in body     # replay preserva o resto (SIP intacto)


# ---------------------------------------------------------------- Intelbras V


@respx.mock
async def test_intelbras_normalize_envia_sysconf_parcial(monkeypatch) -> None:
    ad = IntelbrasAdapter()
    enviado: dict[str, bytes] = {}
    ordem: list[str] = []

    async def _fake_send(ip, creds, cfg, *, fmt="xml"):
        enviado["cfg"] = cfg
        enviado["fmt"] = fmt
        ordem.append("config")

    monkeypatch.setattr(ad, "send_config", _fake_send)

    def _action_resp(request):
        ordem.append("action")
        return httpx.Response(200, text="200 OK Request Success")

    action = respx.get("http://1.2.3.4/cgi-bin/ConfigManApp.com").mock(
        side_effect=_action_resp,
    )

    res = await ad.execute_action("1.2.3.4", _CREDS, "normalize", {})
    assert res.ok
    # o upload de config NÃO reinicia o V-series (uptime confirmado em lab)
    assert not res.rebooted
    # DND é runtime: precisa do Action URI idempotente, não só da config
    assert action.called
    assert action.calls.last.request.url.params.get("key") == "DNDOff"
    assert action.calls.last.request.headers.get("Authorization", "").startswith("Basic ")
    # ORDEM: o firmware engole comandos por ~10s após um upload de config,
    # então o Action URI tem que vir ANTES (medido em lab).
    assert ordem == ["action", "config"]
    cfg = enviado["cfg"].decode()
    # DND off + campainha destravada (campos reais do export do aparelho)
    assert "<EnableDND>0</EnableDND>" in cfg
    assert "<MuteRinging>0</MuteRinging>" in cfg
    # volumes de saída no máximo da escala 0-9
    assert "<HandFreeRingVol>9</HandFreeRingVol>" in cfg
    assert "<HandsetVol>9</HandsetVol>" in cfg
    # ganho de microfone NÃO é tocado (evita eco/microfonia)
    assert "MicVol" not in cfg
    # REGRA INVIOLÁVEL: config parcial nunca carrega seção de rede
    for proibida in ("<net>", "<vpn>", "<dot1x>", "<qos>", "<hotspot>", "WebPort"):
        assert proibida not in cfg


async def test_intelbras_execute_rejeita_acao_desconhecida() -> None:
    with pytest.raises(VendorActionUnsupported):
        await IntelbrasAdapter().execute_action("1.2.3.4", _CREDS, "set_ip", {})


@respx.mock
async def test_intelbras_action_uri_401_vira_auth_error(monkeypatch) -> None:
    """Credencial recusada no Action URI → chain tenta a próxima."""
    from middleware_monitor.integrations.extension_configurator.vendors.base import (
        VendorAuthError,
    )

    ad = IntelbrasAdapter()

    async def _fake_send(ip, creds, cfg, *, fmt="xml"): ...

    monkeypatch.setattr(ad, "send_config", _fake_send)
    respx.get("http://1.2.3.4/cgi-bin/ConfigManApp.com").mock(
        return_value=httpx.Response(401),
    )
    with pytest.raises(VendorAuthError):
        await ad.execute_action("1.2.3.4", _CREDS, "normalize", {})


# ------------------------------------------------- chain de creds (service)


class _ChainAdapter:
    """Fake: recusa a 1ª credencial, aceita a 2ª (telefone já na senha nova)."""

    def __init__(self) -> None:
        self.tried: list[str] = []

    async def execute_action(self, ip, creds, action, params):
        from middleware_monitor.integrations.extension_configurator.vendors.base import (
            VendorAuthError,
        )

        self.tried.append(creds.password)
        if creds.password != "nova":
            raise VendorAuthError("credencial recusada")
        return ActionResult(ok=True, detail="ok")


async def test_execute_with_fallback_tenta_proxima_credencial() -> None:
    from middleware_monitor.domain.extension_configurator.actions import (
        _execute_with_fallback,
    )

    ad = _ChainAdapter()
    chain = [VendorCredentials("admin", "velha"), VendorCredentials("admin", "nova")]
    res = await _execute_with_fallback(ad, "1.2.3.4", chain, "normalize", {})
    assert res.ok
    assert ad.tried == ["velha", "nova"]


async def test_execute_with_fallback_todas_recusadas_levanta_auth_error() -> None:
    from middleware_monitor.domain.extension_configurator.actions import (
        _execute_with_fallback,
    )
    from middleware_monitor.integrations.extension_configurator.vendors.base import (
        VendorAuthError,
    )

    ad = _ChainAdapter()
    chain = [VendorCredentials("admin", "velha"), VendorCredentials("admin", "errada")]
    with pytest.raises(VendorAuthError):
        await _execute_with_fallback(ad, "1.2.3.4", chain, "normalize", {})
    assert ad.tried == ["velha", "errada"]
