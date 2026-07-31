"""Constantes injetadas em build-time. NUNCA commitar segredos reais aqui.

``EMBEDDED_UPDATE_TOKEN_B64`` fica vazio no repositório; o pipeline de release
(``scripts/inject_update_token.py``) reescreve o valor com o base64 de um PAT
fine-grained **somente-leitura** (Contents: Read do repo de releases) para que
os builds distribuídos consigam ler as releases do repositório privado.

O base64 aqui NÃO é segurança — apenas evita que o token apareça cru num grep
casual. A proteção real é o escopo mínimo do token + rotação periódica
(ver docs/RUNBOOK.md).
"""

from __future__ import annotations

import base64

EMBEDDED_UPDATE_TOKEN_B64 = ""


def embedded_update_token() -> str:
    """Retorna o token embutido decodificado, ou string vazia."""
    if not EMBEDDED_UPDATE_TOKEN_B64:
        return ""
    try:
        return base64.b64decode(EMBEDDED_UPDATE_TOKEN_B64).decode("ascii").strip()
    except (ValueError, UnicodeDecodeError):
        return ""
