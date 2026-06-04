"""Testes do IntelbrasS3002Adapter (Configurador de Ramais).

Foco: REGRA INVIOLAVEL (nunca-tocar-em-rede) + protocolo GoAhead do S3002
(login plaintext, replay do form SIP, teclas programaveis, discover). Fixtures
HTML coletadas em lab 2026-06-03 contra 192.168.0.48 (S3002 fw V1.7.0.010412359).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from middleware_monitor.integrations.extension_configurator.vendors import _form_replay
from middleware_monitor.integrations.extension_configurator.vendors.base import (
    VendorCredentials,
)
from middleware_monitor.integrations.extension_configurator.vendors.intelbras_s3002 import (
    _SYSCFG_WHITELIST,
    _WHITELIST,
    IntelbrasS3002Adapter,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Chaves que o aparelho expoe no form SIP mas que NUNCA podem sair com valor
# nosso (devem voltar com o valor atual via replay). Servidores secundarios,
# proxy, STUN e o toggle de DNS-SRV sao "rede" no sentido da regra inviolavel.
_NETWORK_FORBIDDEN = [
    "SecondDomainName", "ProxyServerAddress", "SecondProxyServerAddress",
    "STUNAddress", "PresenceServerAddress", "DNSSRVEnableFlag",
]

_TEMPLATE = {"sip_server": "10.173.1.50"}
_ROW = {
    "conta_sip": "8125", "auth_id": "8125-auth",
    "senha_sip": "et*iw0Rk!%1234?9Mo", "servidor_sip": "10.173.1.50",
    "display_name": "Recepcao", "label": "Recepcao", "account_active": 1,
    "numero_abreviado": "800",
}


def _keys(cfg: bytes) -> set[str]:
    return {ln.split("=", 1)[0] for ln in cfg.decode().splitlines() if "=" in ln}


def _creds():
    return VendorCredentials(username="admin", password="admin")


def _name_html() -> str:
    return (FIXTURES / "s3002_name.html").read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- SIP / rede

def test_generate_config_so_emite_whitelist():
    cfg = IntelbrasS3002Adapter().generate_config(_TEMPLATE, _ROW)
    emitted = _keys(cfg)
    assert emitted <= _WHITELIST, f"fora da whitelist: {emitted - _WHITELIST}"


def test_generate_config_mapeia_campos_sip():
    cfg = IntelbrasS3002Adapter().generate_config(_TEMPLATE, _ROW).decode()
    assert "AccountID=0" in cfg          # conta 1 (0-based)
    # Sip Username == Authenticate Name == auth_id (o PBX exige username = auth)
    assert "UserNumber=8125-auth" in cfg
    assert "appName=8125-auth" in cfg
    assert "Password=et*iw0Rk!%1234?9Mo" in cfg  # plaintext
    assert "DomainName=10.173.1.50" in cfg       # SIP Server
    assert "UserName=Recepcao" in cfg            # Display Name
    assert "EnableAccount=EnableAccount" in cfg


def test_conta_2_mapeia_accountid_1():
    cfg = IntelbrasS3002Adapter().generate_config(
        dict(_TEMPLATE, sip_account="2"), _ROW
    ).decode()
    assert "AccountID=1" in cfg


@pytest.mark.asyncio
async def test_send_config_recusa_chaves_de_rede():
    cfg = b"UserNumber=8125\nProxyServerAddress=1.2.3.4\n"
    with pytest.raises(RuntimeError, match="rede/extra"):
        await IntelbrasS3002Adapter().send_config("127.0.0.1", _creds(), cfg, fmt="xml")


# ------------------------------------------------------ replay anti-rede (real)

def test_replay_preserva_campos_de_rede_e_so_troca_whitelist():
    """Replay do form real de /name.asp: campos de rede voltam intactos e
    SOMENTE a whitelist e sobrescrita com nossos valores."""
    html = _name_html()
    pairs, _ = _form_replay.parse_form_fields(html, IntelbrasS3002Adapter._SIP_FORM)
    original = dict(pairs)
    overrides = {
        "UserNumber": "8125", "Password": "segredo", "DomainName": "10.173.1.50",
    }
    body = _form_replay.merge_body(pairs, overrides, ensure={"Operate": "Submit"})

    # whitelist aplicada
    assert "UserNumber=8125" in body
    assert "DomainName=10.173.1.50" in body
    assert "Operate=Submit" in body
    # campos de rede preservados com o valor ORIGINAL do aparelho (replay)
    for field in _NETWORK_FORBIDDEN:
        if field in original:
            from urllib.parse import quote
            assert f"{field}={quote(original[field], safe='')}" in body, (
                f"{field} deveria voltar com valor original do aparelho"
            )


def test_form_sip_existe_no_fixture():
    pairs, _ = _form_replay.parse_form_fields(_name_html(), IntelbrasS3002Adapter._SIP_FORM)
    names = {n for n, _ in pairs}
    # o form real precisa conter os campos que vamos sobrescrever
    assert {"UserNumber", "Password", "DomainName", "AccountID"} <= names


# ------------------------------------------------------------- teclas (linekey)

def test_linekey_type_table():
    a = IntelbrasS3002Adapter
    assert a._linekey_type_id("line") == 0
    assert a._linekey_type_id("speed_dial") == 5
    assert a._linekey_type_id("blf") == 1
    assert a._linekey_type_id("blf_list") == 11
    assert a._linekey_type_id("7") == 7
    with pytest.raises(ValueError):
        a._linekey_type_id("teletransporte")


def test_render_linekeys_speed_dial_da_linha():
    tpl = {"function_keys": [
        {"key": "Tecla2", "type": "speed_dial", "label": "Central",
         "value_source": "linha", "value_field": "numero_abreviado", "account": 1},
    ]}
    out = IntelbrasS3002Adapter._render_linekeys(tpl, _ROW)
    # ptype=5 (speed dial), pSipAccounts=0 (Conta 1, 0-based), nome, num=800
    assert out[2] == "5,0,Central,800"


def test_generate_config_inclui_linekey():
    tpl = dict(_TEMPLATE, function_keys=[
        {"key": "Tecla3", "type": "speed_dial", "label": "Suporte",
         "value_source": "fixo", "value_fixed": "9000", "account": 2},
    ])
    cfg = IntelbrasS3002Adapter().generate_config(tpl, _ROW).decode()
    assert "LINEKEY3=5,1,Suporte,9000" in cfg  # account 2 -> pSipAccounts=1


def test_linekey_overrides_expande_campos():
    over = IntelbrasS3002Adapter._linekey_overrides({2: "5,0,Central,800"})
    assert over == {
        "ptype2": "5", "pSipAccounts2": "0", "pNAME2": "Central", "pNUM2": "800",
    }


# -------------------------------------------------------------------- discover

# ------------------------------------------------------- credencial web

def test_generate_config_troca_credencial_web_so_quando_pedido():
    # sem nova_web_password -> NAO mexe na credencial
    cfg = IntelbrasS3002Adapter().generate_config(_TEMPLATE, _ROW).decode()
    assert "WEBADMIN_" not in cfg
    # com nova_web_password -> emite usuario + senha
    tpl = dict(_TEMPLATE, nova_web_user="suporte", nova_web_password="S3nh@Forte1")
    cfg = IntelbrasS3002Adapter().generate_config(tpl, _ROW).decode()
    assert "WEBADMIN_USER=suporte" in cfg
    assert "WEBADMIN_PASS=S3nh@Forte1" in cfg


def test_generate_config_credencial_web_user_default_admin():
    tpl = dict(_TEMPLATE, nova_web_password="NovaSenha90")
    cfg = IntelbrasS3002Adapter().generate_config(tpl, _ROW).decode()
    assert "WEBADMIN_USER=admin" in cfg


def test_web_admin_form_ancora_e_role_unico():
    """A pagina de senha (sem name/id no form) e ancoravel por PwdNew e tem
    DOIS grupos de radio `role` — o envio precisa colapsar para um `role=admin`."""
    html = (FIXTURES / "s3002_uphold.html").read_text(encoding="utf-8", errors="replace")
    pairs, _ = _form_replay.parse_form_fields(html, IntelbrasS3002Adapter._WEB_ANCHOR)
    names = [n for n, _ in pairs]
    assert {"UserName", "PwdOld", "PwdNew", "PwdConfirm"} <= set(names)
    assert names.count("role") >= 2  # dois grupos checked -> precisa colapsar
    base = [(n, v) for n, v in pairs if n != "role"]
    body = _form_replay.merge_body(
        [("role", "admin"), *base],
        {"UserName": "admin", "PwdOld": "admin", "PwdNew": "NovaSenha90",
         "PwdConfirm": "NovaSenha90"},
    )
    assert body.count("role=") == 1 and "role=admin" in body
    assert "PwdNew=NovaSenha90" in body and "PwdOld=admin" in body


# --------------------------------------------------------- bloqueio de teclado

def _syscfg_html() -> str:
    return (FIXTURES / "s3002_syscfg.html").read_text(encoding="utf-8", errors="replace")


def test_render_keylock_mapeia_enable():
    r = IntelbrasS3002Adapter._render_keylock
    assert r({}) == []                                   # sem a chave -> nao gerencia
    assert r({"keylock_enable": 0}) == ["LOCKKEYS=0"]    # off
    # Manual (1) -> Bloquear Menu sem auto-lock (timeout 0)
    assert r({"keylock_enable": 1, "keylock_password": "12345"}) == [
        "LOCKKEYS=1", "LOCKTIMEOUT=0", "LOCKPIN=12345",
    ]
    # Ativado (2) -> Bloquear Menu com auto-lock apos keylock_timeout
    assert r({"keylock_enable": 2, "keylock_timeout": 45, "keylock_password": "9999"}) == [
        "LOCKKEYS=1", "LOCKTIMEOUT=45", "LOCKPIN=9999",
    ]


def test_generate_config_inclui_bloqueio_quando_pedido():
    tpl = dict(_TEMPLATE, keylock_enable=2, keylock_timeout=30, keylock_password="4321")
    cfg = IntelbrasS3002Adapter().generate_config(tpl, _ROW).decode()
    assert "LOCKKEYS=1" in cfg
    assert "LOCKTIMEOUT=30" in cfg
    assert "LOCKPIN=4321" in cfg


def test_generate_config_sem_keylock_nao_emite_lock():
    # _TEMPLATE nao traz keylock_enable -> adapter nao toca no bloqueio
    cfg = IntelbrasS3002Adapter().generate_config(_TEMPLATE, _ROW).decode()
    assert "LOCK" not in cfg


@pytest.mark.asyncio
async def test_apply_keylock_recusa_pin_curto():
    # PIN < 4 digitos com bloqueio ativo -> falha cedo, antes de qualquer rede
    with pytest.raises(RuntimeError, match="4 a 15 digitos"):
        await IntelbrasS3002Adapter()._apply_keylock(
            "127.0.0.1", _creds(),
            {"LOCKKEYS": "1", "LOCKTIMEOUT": "30", "LOCKPIN": "123"},
        )


def test_syscfg_replay_preserva_emergencia_e_escopa_aba():
    """Replay do form real /SysConfig.asp: sobrescreve so o bloqueio + currentPage;
    os numeros de emergencia (mesma aba) voltam intactos."""
    from urllib.parse import quote

    html = _syscfg_html()
    pairs, _ = _form_replay.parse_form_fields(html, IntelbrasS3002Adapter._SYSCFG_FORM)
    original = dict(pairs)
    assert {
        "LockKeys", "PhoneLockTimeOut", "PhoneUnlockPIN", "EmergencyCall", "currentPage",
    } <= set(original)

    overrides = {
        "LockKeys": "1", "PhoneLockTimeOut": "30", "PhoneUnlockPIN": "4321",
        "currentPage": IntelbrasS3002Adapter._SYSCFG_CURRENT_PAGE,
    }
    assert set(overrides) <= _SYSCFG_WHITELIST  # nada fora da whitelist
    body = _form_replay.merge_body(pairs, overrides)
    assert "LockKeys=1" in body
    assert "PhoneUnlockPIN=4321" in body
    assert "currentPage=Lockkeys_child" in body
    # numeros de emergencia preservados com o valor atual do aparelho (replay)
    assert f"EmergencyCall={quote(original['EmergencyCall'], safe='')}" in body


def test_parse_home_page():
    html = (FIXTURES / "s3002_home.html").read_text(encoding="utf-8", errors="replace")
    res = IntelbrasS3002Adapter.parse_home_page(html)
    assert res.vendor == "intelbras_s3002"
    assert res.model == "S3002"
    assert res.mac == "d8:36:5f:90:61:19"
    assert res.firmware and res.firmware.startswith("V1.7.0")
    assert res.confidence == 1.0
