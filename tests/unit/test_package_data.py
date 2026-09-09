"""A wheel leva todo arquivo de dados que o código lê do disco (caso de campo 2026-09-09).

O host do cliente, já na 2.12.1 do `.run` Linux, respondia 500 ao abrir qualquer
ambiente HTEK, Intelbras V ou Yealink: `FileNotFoundError` em
`vendors/intelbras_template.xml`. O arquivo existe no repositório, o `.exe`
Windows o leva pelo `.spec`, e a suíte passava — porque roda do código-fonte
(`pip install -e`), onde o arquivo está. Só a wheel, que é o que o instalador
Linux entrega, saía sem ele: `[tool.setuptools.package-data]` listava web,
static e migrations, e nada mais.

Este teste lê o `pyproject.toml` e confere que **cada** arquivo não-Python
dentro de `src/middleware_monitor` casa com algum padrão de `package-data`.
Um template novo, de qualquer fabricante, sem padrão que o cubra, falha aqui —
antes da release, não no cliente.
"""

from __future__ import annotations

import glob
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "src" / "middleware_monitor"

_IGNORAR_SUFIXOS = {".py", ".pyc", ".pyo"}
_IGNORAR_PARTES = {"__pycache__"}


def _arquivos_de_dados() -> set[str]:
    out: set[str] = set()
    for p in PKG.rglob("*"):
        if not p.is_file() or p.suffix in _IGNORAR_SUFIXOS:
            continue
        if _IGNORAR_PARTES & set(p.parts):
            continue
        out.add(p.relative_to(PKG).as_posix())
    return out


def _cobertos_pelo_package_data() -> set[str]:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    padroes = cfg["tool"]["setuptools"]["package-data"]["middleware_monitor"]
    cobertos: set[str] = set()
    for padrao in padroes:
        # setuptools resolve package-data com glob recursivo (`**`), como aqui.
        for hit in glob.glob(padrao, root_dir=PKG, recursive=True):
            if (PKG / hit).is_file():
                cobertos.add(Path(hit).as_posix())
    return cobertos


def test_todo_arquivo_de_dados_do_pacote_entra_na_wheel() -> None:
    dados = _arquivos_de_dados()
    assert dados, "o pacote tem arquivos de dados; se sumiram todos, o teste está olhando o lugar errado"
    faltando = sorted(dados - _cobertos_pelo_package_data())
    assert not faltando, (
        "arquivos que o código lê do disco e a wheel NÃO leva — acrescente um padrão em "
        f"[tool.setuptools.package-data] do pyproject.toml: {faltando}"
    )


def test_templates_dos_fabricantes_estao_cobertos() -> None:
    """O caso concreto do cliente, nomeado — para a mensagem de falha dizer o
    que quebra na tela quando alguém apagar o padrão dos vendors."""
    cobertos = _cobertos_pelo_package_data()
    vendors = PKG / "integrations" / "extension_configurator" / "vendors"
    templates = sorted(p.relative_to(PKG).as_posix() for p in vendors.glob("*_template.*"))
    assert len(templates) >= 3, templates  # htek .xml, intelbras .xml, yealink .cfg
    fora = [t for t in templates if t not in cobertos]
    assert not fora, f"sem estes na wheel, todo ambiente do fabricante responde 500 no Linux: {fora}"
