"""Seleção de conta SIP (1 ou 2) por fabricante — roteamento no generate_config.

Yealink e Intelbras confirmados pelo usuário; HTEK pelos P-codes da pesquisa em
docs públicas (validação final no hardware).
"""

from __future__ import annotations

from typing import Any

from middleware_monitor.domain.extension_configurator.service import build_template
from middleware_monitor.integrations.extension_configurator.vendors import (
    HTEKAdapter,
    IntelbrasAdapter,
    YealinkAdapter,
)

_ROW: dict[str, Any] = {
    "conta_sip": "3677",
    "senha_sip": "Secr3t",
    "label": "Sala",
    "display_name": "Sala",
    "auth_id": "work-3677",
    "account_active": 1,
    "servidor_sip": "192.168.0.172",
    "numero_abreviado": "",
}


def _xml(adapter: Any, sip_account: int) -> str:
    tpl = build_template({"sip_account": sip_account})
    return adapter.generate_config(tpl, dict(_ROW)).decode("utf-8", "replace")


def test_htek_conta1_padrao_inalterada() -> None:
    xml = _xml(HTEKAdapter(), 1)
    assert "<P35 " in xml and "Account1_SipUserId" in xml
    assert "<P735 " not in xml  # nada de conta 2


def test_htek_conta2_usa_pcodes_proprios() -> None:
    xml = _xml(HTEKAdapter(), 2)
    assert "<P735 " in xml and "Account2_SipUserId" in xml
    assert "<P401 " in xml  # Account2_Active
    assert "<P703 " in xml  # Account2_DispalyName
    # não vaza P-codes nem rótulos da conta 1
    assert "<P35 " not in xml and "Account1_" not in xml


def test_intelbras_conta_index() -> None:
    assert '<line index="1">' in _xml(IntelbrasAdapter(), 1)
    assert '<line index="2">' in _xml(IntelbrasAdapter(), 2)


def test_yealink_conta_prefixo() -> None:
    xml1 = _xml(YealinkAdapter(), 1)
    assert "account.1.user_name" in xml1 and "account.2.user_name" not in xml1
    xml2 = _xml(YealinkAdapter(), 2)
    assert "account.2.user_name" in xml2 and "account.1.user_name" not in xml2
