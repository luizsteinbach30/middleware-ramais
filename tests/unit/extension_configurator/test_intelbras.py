"""Testes do adapter Intelbras V-series.

Cobertura:
  - Fingerprint via Server header (com e sem 'rapid logic')
  - Parser de /information.htm com HTML real (extrai modelo/firmware/MAC)
  - Algoritmo de hash do login: md5(user:pwd:nonce)
  - generate_config gera XML com somente as seções permitidas
  - REGRA INVIOLÁVEL: XML não contém <net>/<vpn>/<dot1x>/<qos>/<hotspot>
  - FunctionKey vira <dsskey><dssSoft>
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest
import respx

from middleware_monitor.integrations.extension_configurator.vendors.intelbras import IntelbrasAdapter

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.asyncio
async def test_fingerprint_rapid_logic_matches() -> None:
    a = IntelbrasAdapter()
    with respx.mock(assert_all_called=False) as router:
        router.get("http://192.168.0.154/").mock(
            return_value=httpx.Response(
                401, headers={"Server": "RapidLogic/1.1"}, text="",
            ),
        )
        assert await a.fingerprint("192.168.0.154") == 1.0


@pytest.mark.asyncio
async def test_fingerprint_outro_vendor_zero() -> None:
    a = IntelbrasAdapter()
    with respx.mock(assert_all_called=False) as router:
        router.get("http://192.168.0.99/").mock(
            return_value=httpx.Response(200, headers={"Server": "nginx"}, text="ok"),
        )
        assert await a.fingerprint("192.168.0.99") == 0.0


def test_login_hash_md5_user_pwd_nonce() -> None:
    """Algoritmo descoberto no login.html do V5501:
       md5(user + ':' + pwd + ':' + nonce)"""
    h = IntelbrasAdapter._hash_for_login("admin", "admin", "ba00a8c0000004a7")
    expected = hashlib.md5(b"admin:admin:ba00a8c0000004a7").hexdigest()
    assert h == expected
    assert len(h) == 32  # MD5 hex


def test_parse_information_extrai_modelo_firmware_mac() -> None:
    """HTML do HAR real do V5501 (firmware 2.2.29, MAC 44:3b:32:4c:df:78)."""
    html = """
    <table>
      <tr><td><span id="XSTR_LBL_INFO_MODEL">Model</span>:</td><td>V5501</td></tr>
      <tr><td><span id="XSTR_LBL_INFO_SW">Software</span>:</td><td>2.2.29</td></tr>
      <tr><td><span id="XSTR_LBL_INFO_MAC">MAC</span>:</td>
          <td>44:3b:32:4c:df:78<font id="MacInvalid" style="display:none">(Invalid)</font></td></tr>
    </table>
    """
    r = IntelbrasAdapter.parse_information_page(html)
    assert r.vendor == "intelbras"
    assert r.model == "V5501"
    assert r.firmware == "2.2.29"
    assert r.mac == "44:3b:32:4c:df:78"
    assert r.confidence == 1.0


def test_parse_information_mac_invalido_vira_none() -> None:
    html = '<td><span id="XSTR_LBL_INFO_MAC">MAC</span>:</td><td>nao-eh-mac</td>'
    r = IntelbrasAdapter.parse_information_page(html)
    assert r.mac is None


# === generate_config ===

def _template(nova_user: str = "", nova_pwd: str = "") -> dict:
    return {
        "sip_server": "pbx.example.com",
        "nova_web_user": nova_user,
        "nova_web_password": nova_pwd,
        "menu_password": "456",
        "keylock_password": "789",
        "function_keys": [
            {"key": "LineKey1", "type": "speed_dial", "label": "Central",
             "value_source": "linha", "value_field": "numero_abreviado"},
        ],
    }


def _row() -> dict:
    return {
        "conta_sip": "3660",
        "senha_sip": "s3cret",
        "servidor_sip": "",  # vazio → herda sip_server do template
        "display_name": "Recepcao",
        "auth_id": "3660",
        "numero_abreviado": "9999",
    }


def test_generate_config_inclui_linha_sip_correta() -> None:
    cfg = IntelbrasAdapter().generate_config(_template(), _row())
    root = ET.fromstring(cfg)
    line = root.find("./sip/line[@index='1']")
    assert line is not None
    assert line.findtext("PhoneNumber") == "3660"
    assert line.findtext("RegisterAddr") == "pbx.example.com"
    assert line.findtext("RegisterUser") == "3660"
    assert line.findtext("RegisterPswd") == "s3cret"
    assert line.findtext("DisplayName") == "Recepcao"
    assert line.findtext("EnableReg") == "1"


def test_generate_config_omite_web_admin_quando_nova_senha_nao_definida() -> None:
    """Sem nova_web_password, NAO emite <web><account> — preserva senha atual do aparelho."""
    cfg = IntelbrasAdapter().generate_config(_template(), _row())
    root = ET.fromstring(cfg)
    assert root.find("./web") is None
    phone = root.find("./phone")
    assert phone is not None
    assert phone.findtext("MenuPassword") == "456"
    assert phone.findtext("KeyLockPassword") == "789"


def test_generate_config_keylock_defaults_universais() -> None:
    """Sem keylock_enable/keylock_timeout no template, defaults sao 2/30."""
    cfg = IntelbrasAdapter().generate_config(_template(), _row())
    root = ET.fromstring(cfg)
    phone = root.find("./phone")
    assert phone is not None
    assert phone.findtext("EnableKeyLock") == "2"
    assert phone.findtext("KeyLockTimeout") == "30"


def test_generate_config_keylock_customizado_pelo_ambiente() -> None:
    """Valores do ambiente sobrescrevem os defaults."""
    tpl = _template()
    tpl["keylock_enable"] = 0
    tpl["keylock_timeout"] = 120
    cfg = IntelbrasAdapter().generate_config(tpl, _row())
    root = ET.fromstring(cfg)
    phone = root.find("./phone")
    assert phone.findtext("EnableKeyLock") == "0"
    assert phone.findtext("KeyLockTimeout") == "120"


def test_generate_config_emite_web_admin_quando_nova_senha_definida() -> None:
    """Com nova_web_password preenchida, emite <web><account> com a nova credencial."""
    cfg = IntelbrasAdapter().generate_config(
        _template(nova_user="suporte", nova_pwd="Ambisec@2026"), _row(),
    )
    root = ET.fromstring(cfg)
    acc = root.find("./web/account[@index='1']")
    assert acc is not None
    assert acc.findtext("Name") == "suporte"
    assert acc.findtext("Password") == "Ambisec@2026"


def test_generate_config_emite_web_so_pwd_default_user_admin() -> None:
    """Se so nova_web_password (sem user), usa 'admin' como default user."""
    cfg = IntelbrasAdapter().generate_config(_template(nova_pwd="NovaSenha"), _row())
    root = ET.fromstring(cfg)
    acc = root.find("./web/account[@index='1']")
    assert acc is not None
    assert acc.findtext("Name") == "admin"
    assert acc.findtext("Password") == "NovaSenha"


def test_generate_config_function_key_vira_fkey_fisica() -> None:
    """Validado em V5501 2026-05-20: as teclas DSS FISICAS sao
    <dsskey><internal index='1'><Fkey index='N'>...
    NAO <dssSoft> (que sao soft keys de tela).

    Subtype Discagem rapida (validado 2026-05-21 com backup XML do user):
    Value embute o subtipo como sufixo: `<numero>@<account>/f`.
    """
    cfg = IntelbrasAdapter().generate_config(_template(), _row())
    root = ET.fromstring(cfg)
    # nao deve haver dssSoft
    assert root.find("./dsskey/dssSoft") is None
    # deve haver Fkey index=1 dentro de internal
    fkey = root.find("./dsskey/internal[@index='1']/Fkey[@index='1']")
    assert fkey is not None
    assert fkey.findtext("Type") == "1"          # Memory Key
    assert fkey.findtext("Value") == "9999@1/f"  # numero_abreviado@account/sufixo
    assert fkey.findtext("Title") == "Central"


def test_generate_config_speed_dial_respeita_account_customizado() -> None:
    """Quando function_key define account=2, Value vira `<numero>@2/f`."""
    tpl = _template()
    tpl["function_keys"][0]["account"] = 2
    cfg = IntelbrasAdapter().generate_config(tpl, _row())
    root = ET.fromstring(cfg)
    fkey = root.find("./dsskey/internal[@index='1']/Fkey[@index='1']")
    assert fkey.findtext("Value") == "9999@2/f"


def test_generate_config_value_fixo_speed_dial_recebe_sufixo() -> None:
    """value_source=fixo + speed_dial tambem ganha @account/f no Value."""
    tpl = _template()
    tpl["function_keys"][0] = {
        "key": "LineKey3", "type": "speed_dial", "label": "Suporte",
        "value_source": "fixo", "value_fixed": "1234", "account": 1,
    }
    cfg = IntelbrasAdapter().generate_config(tpl, _row())
    root = ET.fromstring(cfg)
    fkey = root.find("./dsskey/internal[@index='1']/Fkey[@index='3']")
    assert fkey.findtext("Value") == "1234@1/f"


def test_generate_config_line_sem_sufixo() -> None:
    """Type=2 (Line/Conta) usa Value cru — sem sufixo @account/f."""
    tpl = _template()
    tpl["function_keys"][0] = {
        "key": "LineKey1", "type": "line", "label": "",
        "value_source": "fixo", "value_fixed": "SIP1", "account": 1,
    }
    cfg = IntelbrasAdapter().generate_config(tpl, _row())
    root = ET.fromstring(cfg)
    fkey = root.find("./dsskey/internal[@index='1']/Fkey[@index='1']")
    assert fkey.findtext("Type") == "2"
    assert fkey.findtext("Value") == "SIP1"


def test_xml_intelbras_nao_toca_em_rede() -> None:
    """REGRA INVIOLÁVEL: nenhuma seção de rede no XML gerado.

    O Intelbras aplica configs parciais (igual ao HTEK) — o que NÃO
    enviamos fica preservado. Whitelist do que pode aparecer:
      <sip>, <web>, <phone>, <dsskey> (+ raiz <sysConf>)
    PROIBIDO: <net>, <vpn>, <dot1x>, <qos>, <hotspot>, <ap>, <tr069>,
              <log>, <mt> (manutencao/firmware), <call>, <dm>.
    """
    cfg = IntelbrasAdapter().generate_config(_template(), _row())
    root = ET.fromstring(cfg)
    assert root.tag == "sysConf"
    allowed = {"sip", "web", "phone", "dsskey"}
    forbidden = {"net", "vpn", "dot1x", "qos", "hotspot", "ap", "tr069", "mt"}
    found = {child.tag for child in root}
    violations = found - allowed
    forbidden_present = found & forbidden
    assert not forbidden_present, (
        f"PROIBIDO: secoes de rede/firmware no XML: {forbidden_present}"
    )
    assert not violations, (
        f"Secoes nao listadas como permitidas: {violations}. "
        "Se for seguro, adicione ao set ALLOWED no teste e ao htek-style whitelist."
    )


def test_generate_config_servidor_sip_da_linha_sobrescreve_template() -> None:
    """Se a linha tem servidor_sip preenchido, ele sobrescreve o sip_server padrão."""
    row = _row()
    row["servidor_sip"] = "outra.pbx.com"
    cfg = IntelbrasAdapter().generate_config(_template(), row)
    root = ET.fromstring(cfg)
    assert root.find("./sip/line[@index='1']/RegisterAddr").text == "outra.pbx.com"
