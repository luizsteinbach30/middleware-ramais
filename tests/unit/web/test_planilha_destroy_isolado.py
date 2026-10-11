"""Destruir uma planilha não pode derrubar as outras da mesma página.

Por que este teste existe: relato do dono na 2.17.0 — *"quando habilito um DVR no
middleware, ele quebra"*. A tela do ambiente tem a planilha de telefones e a de
DVR/câmeras na mesma página. Trocar de aba DVR/Câmeras (ou ligar o segundo
serviço) destruía a planilha anterior com ``jspreadsheet.destroy(el, true)``. No
jspreadsheet CE 4.x o segundo argumento ``true`` tira os eventos de mouse e
teclado do **documento** e faz ``jexcel = null`` dentro da biblioteca: a planilha
de telefones parava de responder ("Cannot read properties of null (reading
'current')") e a próxima planilha de DVR/câmeras não nascia mais — toast
"Cannot set properties of null (setting 'current')" e as abas em branco. Nenhum
dado era apagado (F5 trazia tudo de volta), mas para quem estava na tela "as
planilhas sumiram".

O teste é textual (não há navegador no CI): nenhum módulo da página chama o
``destroy`` estático com o argumento que zera a biblioteca. Destruir é sempre a
instância: ``sheet.destroy()``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PAGES = Path(__file__).resolve().parents[3] / "src" / "middleware_monitor" / "web" / "static" / "js"

# jspreadsheet.destroy(<qualquer coisa>, <segundo argumento>) / jexcel.destroy(..., ...)
_DESTROY_GLOBAL = re.compile(r"\b(?:jspreadsheet|jexcel)\.destroy\s*\([^,)]*,")


def _modulos() -> list[Path]:
    return sorted(p for p in PAGES.rglob("*.js") if "vendor" not in p.parts)


def test_ha_modulos_para_conferir() -> None:
    assert any(p.name == "extension_configurator_servicos.js" for p in _modulos())


@pytest.mark.parametrize("arquivo", _modulos(), ids=lambda p: p.name)
def test_nenhuma_planilha_zera_o_jspreadsheet_da_pagina(arquivo: Path) -> None:
    codigo = "\n".join(
        linha for linha in arquivo.read_text(encoding="utf-8").splitlines()
        if not linha.lstrip().startswith("//")
    )
    achados = _DESTROY_GLOBAL.findall(codigo)
    assert not achados, (
        f"{arquivo.name}: {achados[0]!r} — o segundo argumento tira os eventos do documento "
        "e zera o jspreadsheet da página inteira; destrua a instância (sheet.destroy())."
    )
