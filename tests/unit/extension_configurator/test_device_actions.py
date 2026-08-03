"""Device actions (v2.7.0): capabilities + execute_action por vendor."""

from __future__ import annotations

from typing import ClassVar

import httpx
import pytest
import respx

from middleware_monitor.integrations.extension_configurator.vendors.base import (
    ACTION_NORMALIZE,
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
    # Intelbras ainda não homologado → capability vazia (oculto na UI)
    assert IntelbrasAdapter().capabilities() == frozenset()


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
