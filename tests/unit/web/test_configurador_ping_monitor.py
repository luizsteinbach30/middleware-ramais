"""Toda ação que mexe no aparelho tem de desligar o monitor de ping ao vivo.

Por que este teste existe: o `apply()` já desligava (`stopMonitor()`, com o
comentário "os aparelhos reiniciam ao aplicar — ping ficaria vermelho à toa"),
mas as duas entradas do **normalizar** — a do menu ⋮ por linha e a do botão em
massa — não desligavam. Relato do dono (2026-08-31): *"em todos os telefones, ao
clicar para realizar a ação de normalizar os aparelhos, não está parando o
ping"*. O efeito é duplo: a coluna de IP fica vermelha à toa em vendor que
reinicia (HTEK sempre; FlyingVoice ao mexer no DND), e o round de ping ainda
concorre com as aplicações em paralelo.

O teste é textual de propósito — não há runtime de navegador no CI (o
`test_static_js_syntax.py` só faz o parse). Ele trava a chamada; a semântica de
`stopMonitor()` é coberta pela própria função.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

JS = (
    Path(__file__).resolve().parents[3]
    / "src" / "middleware_monitor" / "web" / "static" / "js"
    / "pages" / "extension_configurator_detail.js"
)

# Funções que disparam operação NO APARELHO a partir desta tela.
ACOES = ("apply", "runNormalizeOnLine", "normalizeAll")


def _corpo(fonte: str, nome: str) -> str:
    """Corpo da função `nome` — do cabeçalho até o `}` na coluna 0."""
    m = re.search(rf"^(?:async )?function {re.escape(nome)}\(", fonte, re.MULTILINE)
    assert m, f"função {nome}() não encontrada em {JS.name}"
    fim = fonte.find("\n}", m.start())
    assert fim > 0, f"fim da função {nome}() não encontrado"
    return fonte[m.start():fim]


@pytest.mark.parametrize("nome", ACOES)
def test_acao_no_aparelho_desliga_o_monitor_de_ping(nome: str) -> None:
    assert "stopMonitor()" in _corpo(JS.read_text(encoding="utf-8"), nome), (
        f"{nome}() dispara ação no aparelho e precisa chamar stopMonitor(): com o "
        f"monitor ligado, o telefone que reinicia fica vermelho à toa e o ping "
        f"concorre com a aplicação."
    )
