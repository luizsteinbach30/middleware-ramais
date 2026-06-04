"""Testes do adapter Yealink (T3x/T4x — validado em T31G fw 124.86.104.1).

Cobertura:
  - Fingerprint via 302 -> m=mod_listener&p=login (HTTPS)
  - RSA PKCS#1 v1.5 da senha decifra de volta com a chave privada
  - generate_config produz .cfg parcial (account.1 + linekey + fuso)
  - REGRA INVIOLAVEL: nenhuma chave de rede vaza (whitelist de prefixos)
  - FunctionKey vira linekey.<idx>.{type,value,label,line}
  - _parse_upload_result mapeia o JSON do _RES_INFO_
"""

from __future__ import annotations

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa

from middleware_monitor.integrations.extension_configurator.vendors.yealink import (
    YealinkAdapter,
)

IP = "192.168.0.173"


@pytest.mark.asyncio
async def test_fingerprint_redirect_login_matches() -> None:
    a = YealinkAdapter()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/").mock(
            return_value=httpx.Response(
                302,
                headers={"Location": "/servlet?m=mod_listener&p=login&q=loginForm&jumpto=status"},
                text="",
            ),
        )
        assert await a.fingerprint(IP) == 1.0


@pytest.mark.asyncio
async def test_fingerprint_outro_vendor_zero() -> None:
    a = YealinkAdapter()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/").mock(
            return_value=httpx.Response(200, headers={"Server": "nginx"}, text="ok"),
        )
        assert await a.fingerprint(IP) == 0.0


def test_rsa_encrypt_roundtrip() -> None:
    """A senha cifrada com a publica deve decifrar com a privada (PKCS#1 v1.5)."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    pub = priv.public_key().public_numbers()
    n_hex = format(pub.n, "x")
    e_hex = format(pub.e, "x")
    enc_hex = YealinkAdapter._rsa_encrypt_hex("admin", n_hex, e_hex)
    from cryptography.hazmat.primitives.asymmetric import padding

    plain = priv.decrypt(bytes.fromhex(enc_hex), padding.PKCS1v15())
    assert plain == b"admin"


def _template() -> dict:
    return {
        "sip_server": "192.168.0.172",
        "sip_transport": "udp",
        "timezone": "America/Sao_Paulo",
        "function_keys": [
            {
                "key": "LineKey2",
                "type": "speed_dial",
                "label": "Central",
                "value_source": "linha",
                "value_field": "numero_abreviado",
                "account": 1,
            },
        ],
    }


def _row() -> dict:
    return {
        "conta_sip": "3677",
        "senha_sip": "s3cr3t",
        "servidor_sip": "",
        "label": "3677",
        "display_name": "3677",
        "auth_id": "work-3677",
        "numero_abreviado": "800",
        "account_active": 1,
    }


def test_generate_config_conta_sip() -> None:
    cfg = YealinkAdapter().generate_config(_template(), _row()).decode("utf-8")
    assert "account.1.enable = 1" in cfg
    assert "account.1.user_name = 3677" in cfg
    assert "account.1.auth_name = work-3677" in cfg
    assert "account.1.password = s3cr3t" in cfg
    assert "account.1.sip_server.1.address = 192.168.0.172" in cfg
    assert "account.1.sip_server.1.transport_type = 0" in cfg
    assert "local_time.time_zone = -3" in cfg


def test_generate_config_linekey_speed_dial() -> None:
    cfg = YealinkAdapter().generate_config(_template(), _row()).decode("utf-8")
    assert "linekey.2.type = 13" in cfg       # 13 = Speed Dial
    assert "linekey.2.value = 800" in cfg      # vem de numero_abreviado
    assert "linekey.2.label = Central" in cfg
    assert "linekey.2.line = 1" in cfg         # Yealink 1-based: account 1 -> line 1


def test_generate_config_servidor_da_linha_sobrescreve_template() -> None:
    row = _row()
    row["servidor_sip"] = "10.0.0.9"
    cfg = YealinkAdapter().generate_config(_template(), row).decode("utf-8")
    assert "account.1.sip_server.1.address = 10.0.0.9" in cfg


def test_web_admin_troca_senha_quando_configurada() -> None:
    tpl = _template()
    tpl["nova_web_password"] = "Nov@Senha9"
    cfg = YealinkAdapter().generate_config(tpl, _row()).decode("utf-8")
    # user default = admin
    assert "security.user_password = admin:Nov@Senha9" in cfg


def test_web_admin_usuario_custom() -> None:
    tpl = _template()
    tpl["nova_web_user"] = "var"
    tpl["nova_web_password"] = "x1y2z3"
    cfg = YealinkAdapter().generate_config(tpl, _row()).decode("utf-8")
    assert "security.user_password = var:x1y2z3" in cfg


def test_sem_web_admin_nao_mexe_na_credencial() -> None:
    # Sem nova_web_password, NAO emite a atribuicao security.user_password
    # (a citacao no comentario do template nao conta).
    cfg = YealinkAdapter().generate_config(_template(), _row()).decode("utf-8")
    linhas_ativas = [
        ln for ln in cfg.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert not any(ln.strip().startswith("security.user_password") for ln in linhas_ativas)


def test_whitelist_sem_chaves_de_rede() -> None:
    """REGRA INVIOLAVEL: o .cfg gerado so pode ter account.1./linekey./local_time."""
    cfg = YealinkAdapter().generate_config(_template(), _row()).decode("utf-8")
    proibidos = (
        "network.", "static.", "wan_", "lan_", "wifi.", "vpn.", "dot1x.",
        "qos.", "vlan", "dns", "web_server", "http_port",
    )
    for line in cfg.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key = s.split("=", 1)[0].strip().lower()
        assert not any(p in key for p in proibidos), f"chave de rede vazou: {key}"


def test_whitelist_aborta_chave_de_rede() -> None:
    """_assert_whitelist deve levantar se aparecer uma chave de rede."""
    with pytest.raises(RuntimeError, match="whitelist"):
        YealinkAdapter._assert_whitelist("network.internet_port.type = 0\n")


def test_parse_upload_result() -> None:
    p = YealinkAdapter._parse_upload_result
    assert p('<div>{"type":"localcfg","result":"success"}</div>') == "success"
    assert p('{"type":"localcfg","result":"noparam"}') == "noparam"
    assert p('{"result":"failed"}') == "failed"
    # Sucesso real do firmware vem NUMERICO (ex.: result:5 — sem aspas).
    assert p('{"type":"localcfg","result":5}') == "5"
    assert p("<div></div>") is None
