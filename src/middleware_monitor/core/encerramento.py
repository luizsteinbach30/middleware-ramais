"""O pedido de encerramento do aplicativo desktop, visível de qualquer thread.

Mora aqui, e não em ``desktop.py``, por causa do ``.exe``: o PyInstaller roda o
``desktop.py`` como ``__main__``, e quem importa ``middleware_monitor.desktop``
(``updater/instalar.py``, no "Atualizar" da página e no pedido do NOC) ganha
**outra cópia** do módulo, com outro ``threading.Event``. O pedido caía na cópia
que ninguém olhava: o app não fechava, o ajudante antigo esperava o processo
morrer para sempre e abria janelas do cmd em laço (26/09, medido com o ``.exe``
real: o app só saía quando o ajudante novo o matava, 60 s depois). Este módulo é
importado pelo nome do pacote dos dois lados, então é um objeto só.
"""

from __future__ import annotations

import threading

PEDIDO = threading.Event()


def pedir() -> None:
    """Pede para o app fechar. Idempotente; qualquer thread."""
    PEDIDO.set()


def pedido() -> bool:
    return PEDIDO.is_set()
