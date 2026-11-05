"""O middleware como serviço do Windows (ADR 0011): sobe com a máquina, sem ninguém logado.

Serviço roda como ``LocalSystem``, na sessão 0, sem tela. Daí as três mudanças:

- **Programa em ``%ProgramFiles%``:** um caminho fixo para o serviço apontar.
- **Dados em ``%ProgramData%``:** o ``%LOCALAPPDATA%`` do SYSTEM não é o do usuário. A pasta
  guarda ``secret.key`` e o banco, então só SYSTEM e administradores têm acesso.
- **Instalar pede administrador:** quem aciona é a janela do ``.exe``, com o UAC. A atualização
  automática acontece sem ninguém na tela e não tem como pedir essa permissão.

Só biblioteca padrão: ``ctypes`` fala com o gerenciador de serviços, sem pywin32 no ``.exe``.
Este módulo não pode importar ``tkinter`` nem a aplicação (a sessão 0 não tem tela, e as
configurações precisam do ambiente pronto antes do primeiro import).
"""

from __future__ import annotations

import ctypes
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

NOME = "MiddlewareMonitor"
NOME_VISIVEL = "Middleware USCall Monitor"
DESCRICAO = "Painel e monitoramento de ramais do Middleware USCall Monitor."
REGRA_DO_FIREWALL = "Middleware USCall Monitor"
ARGUMENTO_SERVICO = "--servico"
ARGUMENTO_INSTALAR = "--instalar-servico"
ARGUMENTO_REMOVER = "--remover-servico"
ENV_MODO = "MM_MODO_SERVICO"

# Por SID, não por nome: no Windows em português o grupo é "Administradores".
SID_SYSTEM = "*S-1-5-18"
SID_ADMINISTRADORES = "*S-1-5-32-544"

PORTA_PADRAO = 8080
ESPERA_DO_ENCERRAMENTO_S = 60
ESPERA_DA_SAUDE_S = 150
ESPERA_DA_PARADA_S = 40

CREATE_NO_WINDOW = 0x08000000

# dwCurrentState (winsvc.h)
PARADO = 1
INICIANDO = 2
PARANDO = 3
RODANDO = 4


class FalhaNoServico(RuntimeError):
    """Instalar ou remover não terminou. A mensagem vai para a tela do operador."""


def em_servico() -> bool:
    return os.environ.get(ENV_MODO) == "1"


def pasta_dados() -> Path:
    return Path(os.environ.get("ProgramData") or r"C:\ProgramData") / "MiddlewareMonitor"


def caminho_do_programa() -> Path:
    base = os.environ.get("ProgramFiles") or r"C:\Program Files"
    return Path(base) / "MiddlewareMonitor" / "MiddlewareMonitor.exe"


# --- consulta ----------------------------------------------------------------


class _Status(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", ctypes.c_uint32),
        ("dwCurrentState", ctypes.c_uint32),
        ("dwControlsAccepted", ctypes.c_uint32),
        ("dwWin32ExitCode", ctypes.c_uint32),
        ("dwServiceSpecificExitCode", ctypes.c_uint32),
        ("dwCheckPoint", ctypes.c_uint32),
        ("dwWaitHint", ctypes.c_uint32),
    ]


def _advapi() -> Any:
    """``WinDLL`` por ``getattr``: o mypy do CI roda no Linux, onde o nome não existe."""
    dll = getattr(ctypes, "WinDLL")("advapi32", use_last_error=True)  # noqa: B009
    dll.OpenSCManagerW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    dll.OpenSCManagerW.restype = ctypes.c_void_p
    dll.OpenServiceW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    dll.OpenServiceW.restype = ctypes.c_void_p
    dll.QueryServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Status)]
    dll.CloseServiceHandle.argtypes = [ctypes.c_void_p]
    dll.SetServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Status)]
    return dll


def estado(nome: str = NOME) -> int | None:
    """Estado do serviço; ``None`` se não está instalado. Não exige administrador."""
    if not sys.platform.startswith("win"):
        return None
    dll = _advapi()
    gerente = dll.OpenSCManagerW(None, None, 0x0001)  # SC_MANAGER_CONNECT
    if not gerente:
        return None
    try:
        servico = dll.OpenServiceW(gerente, nome, 0x0004)  # SERVICE_QUERY_STATUS
        if not servico:
            return None
        try:
            status = _Status()
            if not dll.QueryServiceStatus(servico, ctypes.byref(status)):
                return None
            return int(status.dwCurrentState)
        finally:
            dll.CloseServiceHandle(servico)
    finally:
        dll.CloseServiceHandle(gerente)


# --- rodar sob o gerenciador de serviços ---------------------------------------


def rodar(corpo: Callable[[], int], pedir_parada: Callable[[], None]) -> int:
    """Entrega o processo ao gerenciador de serviços até ``corpo`` terminar.

    Reporta RODANDO antes de ``corpo``: migration longa não pode estourar os 30 s de partida
    (erro 1053). Quem prova que o painel subiu é o ``/api/system/healthz``. Código diferente
    de zero faz o Windows reiniciar o serviço (``sc failureflag``); zero é parada pedida, e é
    assim que o ajudante da atualização consegue trocar o ``.exe``.
    """
    win_tipo = getattr(ctypes, "WINFUNCTYPE")  # noqa: B009
    dll = _advapi()
    tipo_main = win_tipo(None, ctypes.c_uint32, ctypes.c_void_p)
    tipo_controle = win_tipo(
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p,
    )
    dll.RegisterServiceCtrlHandlerExW.argtypes = [ctypes.c_wchar_p, tipo_controle, ctypes.c_void_p]
    dll.RegisterServiceCtrlHandlerExW.restype = ctypes.c_void_p

    status = _Status(dwServiceType=0x10)  # SERVICE_WIN32_OWN_PROCESS
    alca: list[Any] = [None]

    def informar(atual: int, *, codigo: int = 0, espera_ms: int = 0) -> None:
        status.dwCurrentState = atual
        # 1 = STOP, 4 = SHUTDOWN. Enquanto para, não aceita mais nada.
        status.dwControlsAccepted = 1 | 4 if atual == RODANDO else 0
        status.dwWin32ExitCode = 1066 if codigo else 0  # ERROR_SERVICE_SPECIFIC_ERROR
        status.dwServiceSpecificExitCode = codigo
        status.dwWaitHint = espera_ms
        status.dwCheckPoint = status.dwCheckPoint + 1 if espera_ms else 0
        dll.SetServiceStatus(alca[0], ctypes.byref(status))

    def controle(pedido: int, _tipo: int, _dados: Any, _contexto: Any) -> int:
        if pedido in (1, 5):  # SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN
            informar(PARANDO, espera_ms=ESPERA_DA_PARADA_S * 1000)
            pedir_parada()
        return 0  # NO_ERROR

    controle_c = tipo_controle(controle)

    def principal(_argc: int, _argv: Any) -> None:
        alca[0] = dll.RegisterServiceCtrlHandlerExW(NOME, controle_c, None)
        informar(RODANDO)
        try:
            codigo = corpo()
        except BaseException:  # o corpo já registra a causa; aqui só não pode travar
            codigo = 1
        informar(PARADO, codigo=codigo)

    principal_c = tipo_main(principal)

    class _Entrada(ctypes.Structure):
        _fields_ = [("nome", ctypes.c_wchar_p), ("funcao", tipo_main)]

    tabela = (_Entrada * 2)(_Entrada(NOME, principal_c), _Entrada(None, tipo_main()))
    if not dll.StartServiceCtrlDispatcherW(tabela):
        return 1
    return 0


# --- instalar e remover ------------------------------------------------------


def _executar(comando: list[str], *, conferir: bool = True) -> subprocess.CompletedProcess[bytes]:
    feito = subprocess.run(
        comando, capture_output=True, creationflags=CREATE_NO_WINDOW, check=False,
    )
    if conferir and feito.returncode != 0:
        saida = (feito.stdout + feito.stderr).decode("cp850", "replace").strip()
        raise FalhaNoServico(f"{comando[0]} {comando[1]} falhou ({feito.returncode}): {saida}")
    return feito


def _processo_vivo(pid: int) -> bool:
    if pid <= 0:
        return False
    feito = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
        capture_output=True, creationflags=CREATE_NO_WINDOW, check=False,
    )
    return f'"{pid}"' in feito.stdout.decode("cp850", "replace")


def _porta_ocupada(porta: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", porta)) == 0


def _esperar_a_janela_fechar(pid: int, porta: int) -> None:
    prazo = time.monotonic() + ESPERA_DO_ENCERRAMENTO_S
    while time.monotonic() < prazo:
        if not _processo_vivo(pid) and not _porta_ocupada(porta):
            return
        time.sleep(1.0)
    raise FalhaNoServico(
        f"a porta {porta} continua ocupada. Feche o que estiver usando essa porta e tente de novo.",
    )


def _copiar_dados(origem: Path, destino: Path) -> bool:
    """Copia, e não move: se algo falhar, a instalação antiga continua intacta.

    Nunca sobrescreve: uma pasta de destino com ``secret.key`` já é de um serviço anterior.
    O restritivo vem antes da cópia, para nenhum segredo nascer legível por todos.
    """
    if (destino / "secret.key").exists():
        return False
    destino.mkdir(parents=True, exist_ok=True)
    _executar([
        "icacls", str(destino), "/inheritance:r",
        "/grant:r", f"{SID_SYSTEM}:(OI)(CI)F", f"{SID_ADMINISTRADORES}:(OI)(CI)F",
    ])
    if origem.is_dir():
        shutil.copytree(origem, destino, dirs_exist_ok=True, ignore=shutil.ignore_patterns("tmp"))
    return True


def _criar_servico(exe: Path, porta: int) -> None:
    caminho = f'"{exe}" {ARGUMENTO_SERVICO}'
    _executar([
        "sc.exe", "create", NOME, "binPath=", caminho, "start=", "auto",
        "DisplayName=", NOME_VISIVEL,
    ])
    _executar(["sc.exe", "description", NOME, DESCRICAO])
    # Reinicia após queda, 1 min depois. Zera a contagem depois de um dia.
    _executar([
        "sc.exe", "failure", NOME, "reset=", "86400",
        "actions=", "restart/60000/restart/60000/restart/60000",
    ])
    _executar(["sc.exe", "failureflag", NOME, "1"])
    if porta != PORTA_PADRAO:
        # O APP_PORT do operador não chega ao SYSTEM. O Windows lê o valor ao iniciar o serviço.
        _executar([
            "reg", "add", rf"HKLM\SYSTEM\CurrentControlSet\Services\{NOME}", "/v", "Environment",
            "/t", "REG_MULTI_SZ", "/d", f"APP_PORT={porta}", "/f",
        ])
    # Na sessão 0 não existe o aviso "permitir acesso" do firewall: sem a regra, o painel
    # só abriria na própria máquina.
    _executar([
        "netsh", "advfirewall", "firewall", "add", "rule", f"name={REGRA_DO_FIREWALL}",
        "dir=in", "action=allow", "protocol=TCP", f"localport={porta}", f"program={exe}",
    ])


def _esperar_a_saude(porta: int, versao: str, *, prazo_s: float = ESPERA_DA_SAUDE_S) -> bool:
    prazo = time.monotonic() + prazo_s
    url = f"http://127.0.0.1:{porta}/api/system/healthz"
    while time.monotonic() < prazo:
        try:
            with urllib.request.urlopen(url, timeout=4) as resp:
                if f'"{versao}"' in resp.read(200).decode("utf-8", "replace"):
                    return True
        except OSError:
            pass
        time.sleep(3.0)
    return False


def remover() -> None:
    """Para e apaga o serviço e a regra do firewall. Não apaga dados nem o programa."""
    _executar(["sc.exe", "stop", NOME], conferir=False)
    prazo = time.monotonic() + ESPERA_DA_PARADA_S
    while estado() not in (PARADO, None) and time.monotonic() < prazo:
        time.sleep(1.0)
    _executar(["sc.exe", "delete", NOME], conferir=False)
    _executar(
        ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={REGRA_DO_FIREWALL}"],
        conferir=False,
    )


def instalar(*, origem_exe: Path, dados_origem: Path, pid: int, porta: int, versao: str) -> None:
    """Passa a instalação da janela para o serviço. Roda como administrador.

    Só conta como feito quando o ``healthz`` responde com a versão certa. Antes disso, qualquer
    falha desfaz tudo, e a janela volta a abrir com os dados de sempre.
    """
    if estado() is not None:
        raise FalhaNoServico("o serviço já está instalado.")
    _esperar_a_janela_fechar(pid, porta)

    exe = caminho_do_programa()
    destino = pasta_dados()
    dados_copiados = False
    try:
        exe.parent.mkdir(parents=True, exist_ok=True)
        if origem_exe.resolve() != exe.resolve():
            shutil.copy2(origem_exe, exe)
        dados_copiados = _copiar_dados(dados_origem, destino)
        _criar_servico(exe, porta)
        _executar(["sc.exe", "start", NOME])
        if not _esperar_a_saude(porta, versao):
            raise FalhaNoServico(
                f"o serviço não respondeu em {ESPERA_DA_SAUDE_S} s. Veja {destino}\\logs\\app.log.",
            )
    except (OSError, FalhaNoServico) as exc:
        remover()
        # Uma cópia deixada aqui seria usada na próxima tentativa, já velha.
        if dados_copiados:
            shutil.rmtree(destino, ignore_errors=True)
        if isinstance(exc, FalhaNoServico):
            raise
        raise FalhaNoServico(str(exc)) from exc


def pedir_elevacao(argumentos: Iterable[str]) -> bool:
    """Abre este ``.exe`` como administrador. ``False`` se o operador recusou o UAC."""
    shell32 = getattr(ctypes, "windll").shell32  # noqa: B009
    shell32.ShellExecuteW.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int,
    ]
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    resposta = shell32.ShellExecuteW(
        None, "runas", sys.executable, subprocess.list2cmdline(list(argumentos)), None, 1,
    )
    return int(resposta or 0) > 32
