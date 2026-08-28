"""Defaults para o `config_padrao` de um Ambiente do Configurador de Ramais.

`config_padrao` é JSON serializado em `extension_environments.config_padrao`.
Toda chave nova entra aqui com seu valor default — o repository merge no read.
"""

from __future__ import annotations

from typing import Any

__all__ = ["PHONE_MODELS", "default_config_padrao"]


# Lista de modelos suportados (vendors port em PR2).
PHONE_MODELS: list[str] = [
    "HTEK UC902G",
    "HTEK UC912",
    "HTEK UC924",
    "Intelbras V3001",
    "Intelbras V3101",
    "Intelbras V3501",
    "Intelbras V5501",
    "Intelbras S3002",
    "Intelbras TIP 125i",
    "FlyingVoice P10",
    "Yealink T31G",
]


def default_config_padrao() -> dict[str, Any]:
    """Defaults aplicados quando o ambiente não traz a chave."""
    return {
        "sip_server": "",
        "sip_transport": "udp",
        # Conta SIP do aparelho que recebe a config (1 ou 2). Suporte por
        # fabricante: Yealink confirmado; demais aplicam sempre na conta 1 até
        # o mapeamento da Account 2 ser confirmado.
        "sip_account": 1,
        "web_user": "admin",
        "web_password": "admin",
        "nova_web_user": "",
        "nova_web_password": "",
        # Hora do aparelho. Os dois `*_mode` decidem se este ambiente herda a
        # configuracao da instalacao ou usa a propria: "herdar" (default) faz o
        # telefone receber o fuso detectado do servidor sem ninguem digitar nada.
        # Existe um modo explicito, e nao "campo vazio = herda", porque ambiente
        # antigo ja tem `timezone` gravado no blob (create_environment serializa
        # os defaults inteiros) — sem o modo, filial em Manaus ficaria presa em
        # Sao Paulo para sempre.
        "timezone_mode": "herdar",
        "ntp_mode": "herdar",
        "ntp_server": "a.ntp.br",
        "timezone": "America/Sao_Paulo",
        "web_language": "pt-BR",
        "lcd_language": "pt-BR",
        "register_expiration": 30,
        "validar_conectividade": True,
        # Opt-in: após aplicar, confirma via USCall se o ramal registrou no PBX.
        "verificar_registro_sip": False,
        # Intelbras-only: bloqueio universal do menu habilitado por padrão
        # (EnableKeyLock=2 trava o menu; timeout em segundos).
        "menu_password": "123",
        "keylock_password": "123",
        "keylock_enable": 2,
        "keylock_timeout": 30,
        "function_keys": [
            {
                "key": "LineKey2",
                "type": "speed_dial",
                "label": "Central",
                "value_source": "linha",
                "value_field": "numero_abreviado",
                "value_fixed": "",
                "account": 1,
            },
        ],
    }
