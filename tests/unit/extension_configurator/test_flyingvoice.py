"""Testes do FlyingVoiceAdapter (Configurador de Ramais).

Foco: REGRA INVIOLAVEL (nunca-tocar-em-rede) + as funcionalidades homologadas
no P10 (registro SIP, softkeys, troca de credencial web).
"""

from __future__ import annotations

import pytest

from middleware_monitor.integrations.extension_configurator.vendors.base import (
    VendorCredentials,
)
from middleware_monitor.integrations.extension_configurator.vendors.flyingvoice import (
    _WHITELIST,
    FlyingVoiceAdapter,
)

_NETWORK_FORBIDDEN = [
    "DBID_DNSSRV_DOMAIN", "mwan_ipaddr", "mwan_gateway", "mwan_primary_dns",
    "lan_ipaddr", "dhcpGateway", "ipsec_conn_name", "VPNSRV_AUTOCONN_ENABLE",
    "DBID_WEB_HTTP_DISABLE", "DBID_WEB_PORT", "DBID_WEB_SSL_PORT",
    "DBID_PROFILE_RULE", "PRV_COMMAND_FILE", "DBID_TR_ACS_PWD", "PrvUserName",
]

_TEMPLATE = {"sip_server": "10.173.1.50"}
_ROW = {
    "conta_sip": "assai-8125", "auth_id": "assai-8125",
    "senha_sip": "et*iw0Rk!%1234?9Mo", "servidor_sip": "10.173.1.50",
    "display_name": "8125", "label": "8125", "account_active": 1,
}


def _keys(cfg: bytes) -> set[str]:
    return {ln.split("=", 1)[0] for ln in cfg.decode().splitlines() if "=" in ln}


def _creds():
    return VendorCredentials(username="admin", password="admin")


# ---------------------------------------------------------------- SIP / rede

def test_generate_config_so_emite_whitelist_quando_so_sip():
    cfg = FlyingVoiceAdapter().generate_config(_TEMPLATE, _ROW)
    emitted = _keys(cfg)
    assert emitted <= _WHITELIST, f"fora da whitelist: {emitted - _WHITELIST}"
    for bad in _NETWORK_FORBIDDEN:
        assert bad not in cfg.decode(), f"PROIBIDO emitir chave de rede: {bad}"


def test_generate_config_mapeia_campos_sip():
    cfg = FlyingVoiceAdapter().generate_config(_TEMPLATE, _ROW).decode()
    assert "DBID_SIP_PHONE_NUM=assai-8125" in cfg
    assert "DBID_ALTER_SIP_SERVER_HOSTNAME=10.173.1.50" in cfg
    assert "DBID_SIP_PASSWORD=et*iw0Rk!%1234?9Mo" in cfg   # plaintext
    assert "DBID_SIP_DIS_NAME=8125" in cfg


@pytest.mark.asyncio
async def test_send_config_recusa_chaves_de_rede():
    cfg = b"DBID_SIP_PHONE_NUM=x\nmwan_ipaddr=1.2.3.4\n"
    with pytest.raises(RuntimeError, match="rede/extra"):
        await FlyingVoiceAdapter().send_config("127.0.0.1", _creds(), cfg, fmt="xml")


# ------------------------------------------------------------------ softkeys

def test_softkey_type_table():
    f = FlyingVoiceAdapter
    assert f._softkey_type_id("menu") == 5
    assert f._softkey_type_id("dnd") == 6
    assert f._softkey_type_id("speed_dial") == 8
    assert f._softkey_type_id("33") == 33
    with pytest.raises(ValueError):
        f._softkey_type_id("teletransporte")


def test_render_softkeys_menu_e_speeddial():
    tpl = {"function_keys": [
        {"key": "SoftKey3", "type": "menu"},
        {"key": "SoftKey4", "type": "speed_dial", "label": "Central",
         "value_source": "fixo", "value_fixed": "800", "account": 1},
    ]}
    out = FlyingVoiceAdapter._render_softkeys(tpl, {})
    assert out[3] == "5,,,,"
    # UI "Account 1" (1-based) -> campo `line` 0-based do P10 (0 = Conta 1).
    assert out[4] == "8,0,800,Central,"


def test_render_softkey_account_e_1based():
    # Regressao do off-by-one: Account 2 na tela -> line=1 (Conta 2 no
    # telefone), nao Conta 3. Account 0/ausente cai em Conta 1 (line=0).
    tpl = {"function_keys": [
        {"key": "SoftKey1", "type": "speed_dial", "value_source": "fixo",
         "value_fixed": "900", "account": 2},
        {"key": "SoftKey2", "type": "speed_dial", "value_source": "fixo",
         "value_fixed": "901", "account": 0},
    ]}
    out = FlyingVoiceAdapter._render_softkeys(tpl, {})
    assert out[1] == "8,1,900,,"
    assert out[2] == "8,0,901,,"


def test_generate_config_inclui_softkeys():
    tpl = dict(_TEMPLATE, function_keys=[{"key": "SoftKey3", "type": "dnd"}])
    cfg = FlyingVoiceAdapter().generate_config(tpl, _ROW).decode()
    assert "SOFTKEY3=6,,,," in cfg
    assert "DBID_DNSSRV_DOMAIN" not in cfg and "mwan_" not in cfg


def test_parse_dbid_arrays():
    html = 'x DBIDArray1 = "1,,,,";\nDBIDArray4 = "8,0,800,Central,"; y'
    cur = FlyingVoiceAdapter._parse_dbid_arrays(html)
    assert cur[1] == "1,,,," and cur[4] == "8,0,800,Central,"


# ------------------------------------------------------- credencial web

def test_generate_config_troca_credencial_web_so_quando_pedido():
    # sem nova_web_password -> NAO mexe na credencial
    cfg = FlyingVoiceAdapter().generate_config(_TEMPLATE, _ROW).decode()
    assert "WEBADMIN_" not in cfg
    # com nova_web_password -> emite usuario+senha
    tpl = dict(_TEMPLATE, nova_web_user="suporte", nova_web_password="S3nh@Forte!")
    cfg = FlyingVoiceAdapter().generate_config(tpl, _ROW).decode()
    assert "WEBADMIN_USER=suporte" in cfg
    assert "WEBADMIN_PASS=S3nh@Forte!" in cfg


def test_generate_config_credencial_web_user_default_admin():
    # senha sem usuario -> usuario default 'admin'
    tpl = dict(_TEMPLATE, nova_web_password="NovaSenha9")
    cfg = FlyingVoiceAdapter().generate_config(tpl, _ROW).decode()
    assert "WEBADMIN_USER=admin" in cfg
    assert "WEBADMIN_PASS=NovaSenha9" in cfg
