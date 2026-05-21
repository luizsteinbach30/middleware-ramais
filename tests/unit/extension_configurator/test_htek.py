from pathlib import Path

import httpx
import pytest
import respx

from middleware_monitor.integrations.extension_configurator.vendors.base import VendorCredentials
from middleware_monitor.integrations.extension_configurator.vendors.htek import HTEKAdapter

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.asyncio
async def test_fingerprint_hanlong_matches() -> None:
    adapter = HTEKAdapter()
    with respx.mock(assert_all_called=False) as router:
        router.get("http://192.168.0.41/").mock(
            return_value=httpx.Response(
                401,
                headers={"Server": "HanLong", "WWW-Authenticate": 'Basic realm="IP Phone"'},
                text="<html>...</html>",
            )
        )
        assert await adapter.fingerprint("192.168.0.41") == 1.0


@pytest.mark.asyncio
async def test_fingerprint_other_vendor_does_not_match() -> None:
    adapter = HTEKAdapter()
    with respx.mock(assert_all_called=False) as router:
        router.get("http://192.168.0.50/").mock(
            return_value=httpx.Response(200, headers={"Server": "Yealink"}, text="ok")
        )
        assert await adapter.fingerprint("192.168.0.50") == 0.0


@pytest.mark.asyncio
async def test_fingerprint_network_error_returns_zero() -> None:
    adapter = HTEKAdapter()
    with respx.mock(assert_all_called=False) as router:
        router.get("http://10.0.0.1/").mock(side_effect=httpx.ConnectError("nope"))
        assert await adapter.fingerprint("10.0.0.1") == 0.0


def test_parse_status_page_extracts_uc902g_fields() -> None:
    """Fixture coletado em lab real (2026-05-19) — HTEK UC902G, firmware 2.42.6.5.45R14."""
    html = (FIXTURES / "htek_uc902g_index.html").read_text(encoding="utf-8", errors="replace")
    result = HTEKAdapter.parse_status_page(html)

    assert result.vendor == "htek"
    assert result.model == "UC902G"
    assert result.firmware == "2.42.6.5.45R14"
    assert result.mac == "00:1f:c1:21:cf:23"
    assert result.confidence == 1.0
    assert result.raw is not None
    assert result.raw["wan_ip_address"] == "192.168.0.41"
    assert result.raw["wan_port_type"] == "DHCP"


def test_parse_status_page_empty_html_returns_low_confidence() -> None:
    result = HTEKAdapter.parse_status_page("<html></html>")
    assert result.vendor == "htek"
    assert result.model is None
    assert result.firmware is None
    assert result.mac is None
    assert result.confidence == 0.6


@pytest.mark.asyncio
async def test_discover_against_mocked_index_basic_auth() -> None:
    """Firmware antigo (Basic auth). Discover faz probe em / antes de autenticar."""
    html = (FIXTURES / "htek_uc902g_index.html").read_text(encoding="utf-8", errors="replace")
    adapter = HTEKAdapter()
    creds = VendorCredentials(username="admin", password="admin")
    with respx.mock(assert_all_called=True) as router:
        router.get("http://192.168.0.41/").mock(
            return_value=httpx.Response(
                401,
                headers={"Server": "HanLong", "WWW-Authenticate": 'Basic realm="IP Phone"'},
            )
        )
        router.get("http://192.168.0.41/index.htm").mock(
            return_value=httpx.Response(200, text=html)
        )
        result = await adapter.discover("192.168.0.41", creds)
    assert result.model == "UC902G"
    assert result.firmware == "2.42.6.5.45R14"
    assert result.mac == "00:1f:c1:21:cf:23"


@pytest.mark.asyncio
async def test_discover_against_mocked_index_digest_auth() -> None:
    """Firmware novo (Digest auth, sem Server header). Discover funciona igual."""
    html = (FIXTURES / "htek_uc902g_index.html").read_text(encoding="utf-8", errors="replace")
    adapter = HTEKAdapter()
    creds = VendorCredentials(username="admin", password="admin")
    with respx.mock(assert_all_called=False) as router:
        router.get("http://192.168.0.141/").mock(
            return_value=httpx.Response(
                401,
                headers={"WWW-Authenticate": 'Digest qop="auth", realm="localhost", nonce="abc"'},
            )
        )
        # respx não simula o handshake de Digest; aceita a request autenticada direto
        router.get("http://192.168.0.141/index.htm").mock(
            return_value=httpx.Response(200, text=html)
        )
        result = await adapter.discover("192.168.0.141", creds)
    assert result.model == "UC902G"


def test_generate_config_produces_valid_htek_xml() -> None:
    """Gera config XML no formato hl_provision v1 e valida estrutura/P-codes."""
    import xml.etree.ElementTree as ET

    adapter = HTEKAdapter()
    cfg = adapter.generate_config(
        template={
            "codecs": ["g722", "pcma", "pcmu", "g729"],
            "sip_transport": "udp",
            "register_expiration": 30,
            "timezone": "America/Sao_Paulo",
            "web_language": "pt-BR",
            "lcd_language": "pt-BR",
            "ntp_server": "a.ntp.br",
        },
        row={
            "conta_sip": "3660",
            "senha_sip": "s3cret",
            "servidor_sip": "pbx.example.com",
            "display_name": "Recepção",
            "auth_id": "3660",
        },
    )
    # parse e checa estrutura
    root = ET.fromstring(cfg)
    assert root.tag == "hl_provision"
    config = root.find("config")
    assert config is not None

    # mapa P-code → valor texto
    p_map = {p.tag: p.text for p in config}
    assert p_map["P47"] == "pbx.example.com"        # Sipserver
    assert p_map["P130"] == "0"                     # SipTransport=UDP
    assert p_map["P32"] == "30"                     # RegisterExpiration
    assert p_map["P35"] == "3660"                   # Account1_SipUserId
    assert p_map["P36"] == "3660"                   # Account1_AuthenticateID
    assert p_map["P34"] == "s3cret"                 # Account1_AuthenticatePassword
    assert p_map["P3"] == "Recepção"                # Account1_DispalyName
    assert p_map["P57"] == "9"                      # G.722 = id 9
    assert p_map["P58"] == "8"                      # PCMA = id 8
    assert p_map["P64"] == "18"                     # TimeZone São Paulo
    assert p_map["P30"] == "a.ntp.br"               # NTP server


def test_generate_config_escapes_xml_special_chars() -> None:
    adapter = HTEKAdapter()
    cfg = adapter.generate_config(
        template={},
        row={"conta_sip": "3660", "senha_sip": "p&w<x>", "servidor_sip": "host.x"},
    )
    text = cfg.decode("utf-8")
    assert "p&amp;w&lt;x&gt;" in text
    assert "<P34" in text and "p&w<x>" not in text  # escapado, não literal


def test_generate_config_omite_admin_pwd_quando_nova_senha_nao_definida() -> None:
    """Sem nova_web_password, NAO emite P2/P8681 — preserva senha admin atual."""
    import xml.etree.ElementTree as ET
    adapter = HTEKAdapter()
    cfg = adapter.generate_config(
        template={},
        row={"conta_sip": "1", "senha_sip": "x", "servidor_sip": "y"},
    )
    root = ET.fromstring(cfg)
    p_tags = {p.tag for p in root.find("config")}
    assert "P2" not in p_tags
    assert "P8681" not in p_tags


def test_generate_config_emite_admin_pwd_quando_nova_senha_definida() -> None:
    """Com nova_web_password, emite P2 (AdminPassword) e opcionalmente P8681."""
    import xml.etree.ElementTree as ET
    adapter = HTEKAdapter()
    cfg = adapter.generate_config(
        template={"nova_web_user": "suporte", "nova_web_password": "Ambisec@2026"},
        row={"conta_sip": "1", "senha_sip": "x", "servidor_sip": "y"},
    )
    root = ET.fromstring(cfg)
    p_map = {p.tag: (p.text or "") for p in root.find("config")}
    assert p_map.get("P8681") == "suporte"
    assert p_map.get("P2") == "Ambisec@2026"


def test_generate_config_unknown_codec_raises() -> None:
    adapter = HTEKAdapter()
    with pytest.raises(ValueError, match="codec desconhecido"):
        adapter.generate_config(
            template={"codecs": ["g999"]},
            row={"conta_sip": "1", "senha_sip": "x", "servidor_sip": "y"},
        )
