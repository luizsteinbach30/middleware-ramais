"""Serviço do Windows (ADR 0011): o que dá para provar sem o gerenciador de serviços.

A partida real sob o Windows é provada com o ``.exe``: ver a seção de teste no ADR.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from middleware_monitor import servico_windows as sw
from middleware_monitor.updater.standalone import script_do_ajudante


@pytest.fixture
def maquina(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Máquina falsa: registra os comandos, sem tocar em serviço, firewall ou permissão."""
    monkeypatch.setenv("ProgramData", str(tmp_path / "ProgramData"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "ProgramFiles"))
    comandos: list[list[str]] = []

    def _executar(comando: list[str], *, conferir: bool = True) -> subprocess.CompletedProcess[bytes]:
        comandos.append(comando)
        return subprocess.CompletedProcess(comando, 0, b"", b"")

    monkeypatch.setattr(sw, "_executar", _executar)
    monkeypatch.setattr(sw, "estado", lambda nome=sw.NOME: None)
    monkeypatch.setattr(sw, "_esperar_a_janela_fechar", lambda pid, porta: None)
    monkeypatch.setattr(sw, "_esperar_a_saude", lambda porta, versao: True)
    return comandos


def _instalar(tmp_path: Path) -> None:
    exe = tmp_path / "baixado" / "MiddlewareMonitor-9.9.9.exe"
    exe.parent.mkdir(exist_ok=True)
    exe.write_bytes(b"exe")
    dados = tmp_path / "usuario"
    (dados / "db").mkdir(parents=True, exist_ok=True)
    (dados / "tmp").mkdir(exist_ok=True)
    (dados / "secret.key").write_text("segredo", encoding="ascii")
    (dados / "db" / "app.db").write_text("banco", encoding="ascii")
    (dados / "tmp" / "lixo").write_text("x", encoding="ascii")
    sw.instalar(origem_exe=exe, dados_origem=dados, pid=1, porta=8080, versao="9.9.9")


def test_instalar_copia_programa_e_dados_sem_apagar_a_origem(maquina, tmp_path) -> None:
    _instalar(tmp_path)

    assert sw.caminho_do_programa().read_bytes() == b"exe"
    assert (sw.pasta_dados() / "secret.key").read_text(encoding="ascii") == "segredo"
    assert (sw.pasta_dados() / "db" / "app.db").exists()
    assert not (sw.pasta_dados() / "tmp").exists()
    assert (tmp_path / "usuario" / "secret.key").exists()


def test_permissao_restrita_vem_antes_da_copia_e_por_sid(maquina, tmp_path) -> None:
    _instalar(tmp_path)

    icacls = next(c for c in maquina if c[0] == "icacls")
    assert "/inheritance:r" in icacls
    assert any(sw.SID_SYSTEM in c for c in icacls)
    assert any(sw.SID_ADMINISTRADORES in c for c in icacls)
    assert maquina.index(icacls) < next(i for i, c in enumerate(maquina) if c[1] == "create")


def test_servico_automatico_aponta_para_o_programa_e_reinicia_em_falha(maquina, tmp_path) -> None:
    _instalar(tmp_path)

    criar = next(c for c in maquina if c[:2] == ["sc.exe", "create"])
    assert criar[criar.index("start=") + 1] == "auto"
    assert criar[criar.index("binPath=") + 1] == f'"{sw.caminho_do_programa()}" --servico'
    assert ["sc.exe", "failureflag", sw.NOME, "1"] in maquina
    assert any(c[:2] == ["sc.exe", "failure"] for c in maquina)
    regra = next(c for c in maquina if c[0] == "netsh")
    assert "localport=8080" in regra
    assert f"program={sw.caminho_do_programa()}" in regra


def test_nao_sobrescreve_dados_de_um_servico_anterior(maquina, tmp_path) -> None:
    sw.pasta_dados().mkdir(parents=True)
    (sw.pasta_dados() / "secret.key").write_text("do servico", encoding="ascii")

    _instalar(tmp_path)

    assert (sw.pasta_dados() / "secret.key").read_text(encoding="ascii") == "do servico"
    assert not any(c[0] == "icacls" for c in maquina)


def test_servico_que_nao_responde_desfaz_tudo(maquina, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sw, "_esperar_a_saude", lambda porta, versao: False)

    with pytest.raises(sw.FalhaNoServico, match="não respondeu"):
        _instalar(tmp_path)

    assert ["sc.exe", "delete", sw.NOME] in maquina
    assert any(c[:5] == ["netsh", "advfirewall", "firewall", "delete", "rule"] for c in maquina)
    assert not sw.pasta_dados().exists()
    assert (tmp_path / "usuario" / "secret.key").exists()


def test_falha_nao_apaga_dados_que_ja_eram_do_servico(maquina, tmp_path, monkeypatch) -> None:
    sw.pasta_dados().mkdir(parents=True)
    (sw.pasta_dados() / "secret.key").write_text("do servico", encoding="ascii")
    monkeypatch.setattr(sw, "_esperar_a_saude", lambda porta, versao: False)

    with pytest.raises(sw.FalhaNoServico):
        _instalar(tmp_path)

    assert (sw.pasta_dados() / "secret.key").exists()


def test_recusa_instalar_por_cima_de_um_servico(maquina, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sw, "estado", lambda nome=sw.NOME: sw.RODANDO)

    with pytest.raises(sw.FalhaNoServico, match="já está instalado"):
        _instalar(tmp_path)

    assert maquina == []


def _ajudante(servico: str) -> str:
    base = Path("C:/x")
    return script_do_ajudante(
        novo=base / "novo.exe", atual=base / "atual.exe", alvo="9.9.9", porta=8080,
        resultado=base / "r.txt", trava=base / "t.lock", servico=servico,
    )


def test_ajudante_da_atualizacao_sobe_o_servico_e_nao_a_janela() -> None:
    script = _ajudante(sw.NOME)

    assert f"Start-Service -Name '{sw.NOME}'" in script
    assert "Start-Process" not in script


def test_ajudante_sem_servico_segue_abrindo_o_exe() -> None:
    script = _ajudante("")

    assert "Start-Process -FilePath $atual" in script
    assert "Start-Service" not in script


def test_dados_do_servico_ficam_na_pasta_da_maquina(tmp_path, monkeypatch) -> None:
    pytest.importorskip("tkinter", exc_type=ImportError)
    from middleware_monitor.desktop import _user_data_dir

    monkeypatch.setenv("ProgramData", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "usuario"))
    monkeypatch.setenv(sw.ENV_MODO, "1")
    assert _user_data_dir() == tmp_path / "MiddlewareMonitor"

    monkeypatch.delenv(sw.ENV_MODO)
    assert _user_data_dir() != tmp_path / "MiddlewareMonitor"


def test_porta_diferente_da_padrao_chega_ao_servico(maquina, tmp_path) -> None:
    exe = tmp_path / "x.exe"
    exe.write_bytes(b"exe")

    sw.instalar(origem_exe=exe, dados_origem=tmp_path / "nada", pid=1, porta=8099, versao="9.9.9")

    reg = next(c for c in maquina if c[0] == "reg")
    assert "APP_PORT=8099" in reg


def test_porta_padrao_nao_grava_ambiente(maquina, tmp_path) -> None:
    _instalar(tmp_path)

    assert not any(c[0] == "reg" for c in maquina)


def test_corpo_do_servico_devolve_codigo_de_falha_quando_o_servidor_cai(monkeypatch, tmp_path) -> None:
    pytest.importorskip("tkinter", exc_type=ImportError)
    from middleware_monitor import desktop
    from middleware_monitor.core import encerramento

    class _SemSubir:
        thread = None
        error = RuntimeError("porta ocupada")

        def __init__(self, host: str, port: int) -> None: ...
        def start(self) -> None: ...

    monkeypatch.setattr(desktop, "ServerThread", _SemSubir)
    monkeypatch.setattr(desktop, "_bootstrap_env", lambda: None)
    monkeypatch.setattr(desktop, "_user_data_dir", lambda: tmp_path)
    monkeypatch.setenv("APP_HOST", "127.0.0.1")
    monkeypatch.setenv("APP_PORT", "8080")
    (tmp_path / "logs").mkdir()
    encerramento.PEDIDO.clear()
    raiz = logging.getLogger()
    antes = list(raiz.handlers)

    try:
        assert desktop._corpo_do_servico() == 1
    finally:
        for handler in [h for h in raiz.handlers if h not in antes]:
            raiz.removeHandler(handler)
            handler.close()
