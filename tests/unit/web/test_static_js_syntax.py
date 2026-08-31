"""Todo módulo JS servido pela web tem de ser sintaticamente válido.

Por que este teste existe: a tela **Config padrão** do Configurador de Ramais
ficou quebrada da v2.8.0 até a v2.10.0 por um `await` dentro de uma função não
`async` (`collectFKs`), colado no lugar errado ao trazer a hora herdada do
servidor (`3005ae0`, PR #45). É `SyntaxError`, então o navegador **descarta o
módulo inteiro** — nenhum handler é registrado. O sintoma que chegou não parecia
sintaxe: "ao clonar vem tudo zerado" (o `reload()` nunca rodou para preencher os
campos) e "ao salvar não redireciona" (o `click` do Salvar nunca foi ligado).

Nada no pipeline olhava para o JavaScript: `ruff` e `mypy` só veem Python, e os
testes exercitam a API, nunca a página. Um arquivo `.js` inválido passava por
CI verde, virava release e só aparecia no navegador do usuário.

O parse é feito pelo `node`, que é o mesmo motor que roda no navegador. Sem
`node` o teste é pulado — mas o CI garante a presença dele (passo "Setup Node"
em `.github/workflows/ci.yml`), então no pipeline ele sempre roda de verdade.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

JS_ROOT = (
    Path(__file__).resolve().parents[3]
    / "src" / "middleware_monitor" / "web" / "static" / "js"
)

# Bibliotecas de terceiros entram no bundle como vieram; não é papel deste teste
# julgar o código delas (e várias são scripts clássicos, não módulos ES).
_IGNORADOS = ("vendor/",)


def _modulos() -> list[Path]:
    return sorted(
        p for p in JS_ROOT.rglob("*.js")
        if not any(parte in p.as_posix() for parte in _IGNORADOS)
    )


def test_ha_modulos_para_checar() -> None:
    """Guarda contra o teste virar no-op se a pasta for movida de lugar."""
    assert JS_ROOT.is_dir(), f"pasta de JS não encontrada: {JS_ROOT}"
    assert len(_modulos()) >= 10, "poucos módulos encontrados — o caminho mudou?"


@pytest.mark.skipif(shutil.which("node") is None, reason="node não instalado")
@pytest.mark.parametrize("caminho", _modulos(), ids=lambda p: p.name)
def test_modulo_js_parseia(caminho: Path) -> None:
    """`node --check` no arquivo: é o parse do próprio motor do navegador."""
    proc = subprocess.run(
        [shutil.which("node") or "node", "--input-type=module", "--check"],
        input=caminho.read_bytes(),
        capture_output=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        erro = proc.stderr.decode("utf-8", errors="replace")
        # O navegador descarta o módulo inteiro: a página inteira para de funcionar.
        pytest.fail(
            f"{caminho.relative_to(JS_ROOT)} não parseia — a página que o importa "
            f"não vai carregar nenhum handler:\n{erro}",
        )
