"""Falhas de boot do ServerThread precisam virar erro visível (v2.7.2).

Regressão da v2.7.0/v2.7.1 em campo: o release instalou structlog 26, que
quebra com `sys.stdout = None` (exe `console=False`). O crash acontecia no
lifespan do uvicorn, que respondia com `sys.exit(3)` — e `SystemExit` não é
`Exception`, então escapava do `except` da thread do servidor. Resultado: a
thread morria sem setar `error` e a janela ficava em "Iniciando..." para
sempre, sem log (o `fileConfig` do env.py do Alembic ainda desligava os
handlers no meio do boot).
"""

from __future__ import annotations

import time

import pytest

from middleware_monitor.desktop import ServerThread


@pytest.fixture
def crash_dir(tmp_path, monkeypatch):
    """Aponta o boot-crash.log para um diretório descartável."""
    monkeypatch.setattr("middleware_monitor.desktop._user_data_dir", lambda: tmp_path)
    return tmp_path


def _aguarda_erro(st: ServerThread, timeout: float = 5.0) -> None:
    inicio = time.monotonic()
    while st.error is None and time.monotonic() - inicio < timeout:
        time.sleep(0.02)


def test_excecao_no_bootstrap_seta_error(crash_dir, monkeypatch) -> None:
    st = ServerThread("127.0.0.1", 1)
    monkeypatch.setattr(
        ServerThread,
        "_bootstrap_database",
        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("db quebrou"))),
    )
    st.start()
    assert isinstance(st.error, RuntimeError)
    assert st.is_running() is False


def test_systemexit_no_bootstrap_nao_escapa(crash_dir, monkeypatch) -> None:
    """uvicorn usa sys.exit() para abortar; SystemExit não pode matar a
    thread em silêncio nem derrubar o processo da UI."""
    st = ServerThread("127.0.0.1", 1)
    monkeypatch.setattr(
        ServerThread,
        "_bootstrap_database",
        staticmethod(lambda: (_ for _ in ()).throw(SystemExit(3))),
    )
    st.start()  # não pode propagar
    assert isinstance(st.error, SystemExit)
    assert st.is_running() is False


def test_falha_gera_boot_crash_log(crash_dir, monkeypatch) -> None:
    st = ServerThread("127.0.0.1", 1)
    monkeypatch.setattr(
        ServerThread,
        "_bootstrap_database",
        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("explodiu aqui"))),
    )
    st.start()
    crash = crash_dir / "logs" / "boot-crash.log"
    assert crash.exists()
    assert "explodiu aqui" in crash.read_text(encoding="utf-8")


def test_systemexit_dentro_do_runner_seta_error(crash_dir, monkeypatch) -> None:
    """Cenário exato da v2.7.1: bootstrap ok, server.run() -> sys.exit(3)."""

    class ServidorQueAborta:
        started = False

        def run(self) -> None:
            raise SystemExit(3)

    st = ServerThread("127.0.0.1", 1)
    monkeypatch.setattr(ServerThread, "_bootstrap_database", staticmethod(lambda: None))

    import types

    fake_uvicorn = types.SimpleNamespace(
        Config=lambda *a, **k: None,
        Server=lambda _cfg: ServidorQueAborta(),
    )
    monkeypatch.setitem(__import__("sys").modules, "uvicorn", fake_uvicorn)
    monkeypatch.setitem(
        __import__("sys").modules,
        "middleware_monitor.app",
        types.SimpleNamespace(create_app=object),
    )

    st.start()
    assert st.thread is not None
    st.thread.join(timeout=5)
    _aguarda_erro(st)
    assert isinstance(st.error, SystemExit)
    assert st.is_running() is False
    crash = crash_dir / "logs" / "boot-crash.log"
    assert crash.exists()
    assert "server_crashed" in crash.read_text(encoding="utf-8")
