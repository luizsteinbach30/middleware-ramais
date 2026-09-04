"""Modo de atualização (v2.12.0): quem aplica o "Atualizar agora" fora do .exe.

No Linux instalado pelo ``.run`` o serviço não pode se atualizar (usuário sem
privilégio, /opt somente-leitura): ele pede à unidade systemd. ``auto`` decide
pela presença do env que o instalador cria.
"""

from __future__ import annotations

from pathlib import Path

from middleware_monitor import settings as settings_mod
from middleware_monitor.settings import Settings


def test_auto_sem_instalacao_linux_e_legacy(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_mod, "LINUX_INSTALL_MARKER", tmp_path / "nao-existe")
    assert Settings(update_mode="auto").resolved_update_mode() == "legacy"


def test_auto_com_env_do_run_e_systemd(monkeypatch, tmp_path: Path) -> None:
    marker = tmp_path / "env"
    marker.write_text("APP_PORT=8080\n", encoding="utf-8")
    monkeypatch.setattr(settings_mod, "LINUX_INSTALL_MARKER", marker)
    assert Settings(update_mode="auto").resolved_update_mode() == "systemd"


def test_modo_explicito_vence_a_deteccao(monkeypatch, tmp_path: Path) -> None:
    marker = tmp_path / "env"
    marker.write_text("x=1\n", encoding="utf-8")
    monkeypatch.setattr(settings_mod, "LINUX_INSTALL_MARKER", marker)
    assert Settings(update_mode="legacy").resolved_update_mode() == "legacy"
    assert Settings(update_mode="SYSTEMD").resolved_update_mode() == "systemd"
