"""O "Atualizar" da página e o pedido do NOC fecham o app do .exe (26/09).

No .exe o PyInstaller roda ``desktop.py`` como ``__main__``; ``updater/instalar.py``
importa ``middleware_monitor.desktop`` e ganha OUTRA cópia do módulo. Com o evento
de encerramento dentro do ``desktop.py``, o pedido caía na cópia que a janela não
olhava: o app nunca fechava, e o ajudante antigo abria janelas do cmd em laço
esperando o processo morrer. Medido com o .exe real antes da correção: o app só
saía quando o ajudante novo o matava, 60 s depois.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from middleware_monitor.core import encerramento

pytest.importorskip("tkinter")


@pytest.fixture(autouse=True)
def _limpo() -> Iterator[None]:
    encerramento.PEDIDO.clear()
    yield
    encerramento.PEDIDO.clear()


def _desktop_como_main() -> object:
    """Uma segunda cópia do desktop.py, como o PyInstaller a carrega (``__main__``)."""
    import middleware_monitor

    caminho = Path(middleware_monitor.__file__).parent / "desktop.py"
    spec = importlib.util.spec_from_file_location("__main_do_exe__", caminho)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["__main_do_exe__"] = modulo
    try:
        spec.loader.exec_module(modulo)
    finally:
        sys.modules.pop("__main_do_exe__", None)
    return modulo


def test_pedido_do_painel_chega_a_janela_mesmo_com_o_desktop_em_duas_copias() -> None:
    principal = _desktop_como_main()  # a cópia em que a janela Tk roda
    import middleware_monitor.desktop as importado  # a cópia que o instalar() importa

    assert principal is not importado
    assert not encerramento.pedido()
    # O caminho do "Atualizar" da página / do NOC:
    from middleware_monitor.core.encerramento import pedir

    pedir()
    # O que a janela consulta em _poll_shutdown:
    assert encerramento.pedido()
    # E o botão da bandeja (request_shutdown da cópia principal) cai no mesmo lugar.
    encerramento.PEDIDO.clear()
    principal.request_shutdown()  # type: ignore[attr-defined]
    assert encerramento.pedido()


def test_instalar_usa_o_pedido_compartilhado() -> None:
    import middleware_monitor.updater.instalar as instalar

    fonte = Path(instalar.__file__).read_text(encoding="utf-8")
    assert "from middleware_monitor.core.encerramento import pedir as request_shutdown" in fonte
    assert "from middleware_monitor.desktop import get_data_dir, request_shutdown" not in fonte
