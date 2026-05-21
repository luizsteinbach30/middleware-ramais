"""Servicos auxiliares do Configurador de Ramais.

Funcoes puras (sem side-effect no DB) para:
  - escolher o adapter pelo `modelo_telefone` do ambiente
  - construir o `template` e o `row` que vao para `adapter.generate_config`
  - calcular o sha256 do config gerado por linha
  - determinar `pending`/`applied`/`outdated`/`error` por linha

O fluxo de aplicacao em massa fica em `apply.py`.
"""

from __future__ import annotations

import hashlib
from typing import Any

from middleware_monitor.core.models import ExtensionEnvironment, ExtensionLine
from middleware_monitor.integrations.extension_configurator.vendors import (
    HTEKAdapter,
    IntelbrasAdapter,
    VendorAdapter,
)

from .repository import merged_config_padrao


def adapter_for(modelo_telefone: str) -> VendorAdapter:
    """Decide o adapter pelo modelo cadastrado no ambiente.

    Modelo cadastrado pelo usuario eh fonte da verdade — nao fazemos
    discover automatico para nao bater no aparelho (feedback-nao-bater).
    """
    if modelo_telefone.lower().startswith("intelbras"):
        return IntelbrasAdapter()
    return HTEKAdapter()


def build_template(cfg: dict[str, Any]) -> dict[str, Any]:
    """Subset de `config_padrao` que vai para `adapter.generate_config`."""
    return {
        "sip_server": cfg.get("sip_server", ""),
        "sip_transport": cfg.get("sip_transport", "udp"),
        "register_expiration": cfg.get("register_expiration", 30),
        "ntp_server": cfg.get("ntp_server", "a.ntp.br"),
        "timezone": cfg.get("timezone", "America/Sao_Paulo"),
        "web_language": cfg.get("web_language", "pt-BR"),
        "lcd_language": cfg.get("lcd_language", "pt-BR"),
        "function_keys": cfg.get("function_keys", []),
        "nova_web_user": cfg.get("nova_web_user", ""),
        "nova_web_password": cfg.get("nova_web_password", ""),
        "menu_password": cfg.get("menu_password", "123"),
        "keylock_password": cfg.get("keylock_password", cfg.get("menu_password", "123")),
        "keylock_enable": cfg.get("keylock_enable", 2),
        "keylock_timeout": cfg.get("keylock_timeout", 30),
    }


def build_row(line: ExtensionLine, cfg: dict[str, Any]) -> dict[str, Any]:
    """Junta dados da linha com defaults do ambiente.

    `nome_visivel` (vazio = numero_ramal) vira label/display do telefone.
    `servidor_sip` vazio na linha herda `sip_server` do ambiente.
    """
    nome = line.nome_visivel or line.numero_ramal
    return {
        "conta_sip": line.numero_ramal,
        "senha_sip": line.senha_sip,
        "servidor_sip": line.servidor_sip or cfg.get("sip_server", ""),
        "label": nome,
        "display_name": nome,
        "auth_id": line.user_auth or line.numero_ramal,
        "numero_abreviado": line.numero_abreviado,
        "account_active": 1,
    }


def compute_line_hash(env: ExtensionEnvironment, line: ExtensionLine) -> str:
    """sha256 hex do XML que seria gerado para esta linha agora."""
    cfg = merged_config_padrao(env)
    adapter = adapter_for(env.modelo_telefone)
    payload = adapter.generate_config(build_template(cfg), build_row(line, cfg))
    return hashlib.sha256(payload).hexdigest()


def line_status(line: ExtensionLine, hash_atual: str) -> str:
    """Estado: pending | applied | outdated | error."""
    if line.ultimo_status is None:
        return "pending"
    if line.ultimo_status == "erro":
        return "error"
    if line.ultimo_hash_aplicado == hash_atual:
        return "applied"
    return "outdated"


def compute_statuses(
    env: ExtensionEnvironment, lines: list[ExtensionLine],
) -> list[dict[str, str]]:
    """`[{id, hash_atual, status}]` por linha. Linhas sem IP ficam `pending`."""
    out: list[dict[str, str]] = []
    for ln in lines:
        if not ln.ip:
            out.append({"id": ln.id, "hash_atual": "", "status": "pending"})
            continue
        h = compute_line_hash(env, ln)
        out.append({"id": ln.id, "hash_atual": h, "status": line_status(ln, h)})
    return out


def pick_lines_to_apply(
    env: ExtensionEnvironment,
    lines: list[ExtensionLine],
    *,
    force: bool,
    selected_ids: list[str] | None,
) -> list[tuple[ExtensionLine, str]]:
    """Devolve `[(line, hash_esperado)]` que vao para o pipeline.

    - linhas sem IP sao puladas (nao tem onde aplicar);
    - se `selected_ids` for dado, processa SO essas linhas (ignora status);
    - senao com `force=True` processa todas com IP;
    - senao pula linhas ja `applied` (hash atual == ultimo aplicado).
    """
    cfg = merged_config_padrao(env)
    adapter = adapter_for(env.modelo_telefone)
    template = build_template(cfg)
    wanted = set(selected_ids or [])
    out: list[tuple[ExtensionLine, str]] = []
    for ln in lines:
        if not ln.ip:
            continue
        if selected_ids is not None and ln.id not in wanted:
            continue
        payload = adapter.generate_config(template, build_row(ln, cfg))
        h = hashlib.sha256(payload).hexdigest()
        if selected_ids is None and not force and line_status(ln, h) == "applied":
            continue
        out.append((ln, h))
    return out
