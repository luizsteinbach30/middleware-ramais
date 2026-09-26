"""Atualização pedida pelo NOC (ADR 0008, CONTRATO-DO-AGENTE §12).

Cada teste é uma trava: se uma delas deixar de valer, a frota inteira sente —
instalando no horário comercial, descendo de versão, ou tentando para sempre.
"""

from __future__ import annotations

import sys
import threading
import time as relogio
from datetime import datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from middleware_monitor.updater import automatico as a
from middleware_monitor.updater import standalone as st
from middleware_monitor.updater.standalone import ler_resultado, script_do_ajudante

AGORA = datetime(2026, 9, 26, 3, 30)  # 03:30, dentro da janela 02:00 a 05:00
JANELA = dict(janela_inicio=time(2, 0), janela_fim=time(5, 0))
# Id cujo espalhamento é pequeno; o teste do espalhamento usa outro.
AGENTE = next(f"ag_{i}" for i in range(1000) if a.espalhamento_min(f"ag_{i}", 180) < 10)


def pedido(versao: str | None = "2.14.0", agora: str | None = None, **janela: time) -> a.Pedido:
    return a.Pedido(versao, **{**JANELA, **janela}, agora_pedido_em=agora)


def decidir(atual: a.Estado | None = None, p: a.Pedido | None = None, **kw: object) -> a.Decisao:
    args: dict[str, object] = dict(
        versao_instalada="2.13.0",
        agora_utc=AGORA,
        agora_local=AGORA,
        agente_id=AGENTE,
        ocupado=None,
        ligada=True,
    )
    args.update(kw)
    return a.decidir(atual or a.Estado(), p or pedido(), **args)  # type: ignore[arg-type]


# --- Versão ------------------------------------------------------------------------------------


def test_na_janela_ocioso_e_desatualizado_instala() -> None:
    d = decidir()
    assert d.instalar
    assert (d.estado.estado, d.estado.alvo, d.estado.tentativas) == (a.INSTALANDO, "2.14.0", 1)


def test_mesma_versao_e_em_dia() -> None:
    d = decidir(versao_instalada="2.14.0")
    assert not d.instalar and d.estado.estado == a.EM_DIA


def test_so_sobe_desejada_menor_nao_faz_nada() -> None:
    d = decidir(versao_instalada="2.15.0")
    assert not d.instalar and d.estado.estado == a.ACIMA


def test_sem_versao_desejada_nao_faz_nada() -> None:
    assert not decidir(p=pedido(versao=None)).instalar


def test_desligada_no_cliente() -> None:
    d = decidir(ligada=False)
    assert not d.instalar and d.estado.estado == a.DESLIGADA


# --- Janela ------------------------------------------------------------------------------------


def test_fora_da_janela_espera() -> None:
    d = decidir(agora_local=datetime(2026, 9, 26, 14, 0))
    assert not d.instalar and d.estado.estado == a.AGUARDANDO
    assert "02:00" in d.estado.detalhe


def test_janela_que_cruza_a_meia_noite() -> None:
    p = pedido(janela_inicio=time(23, 0), janela_fim=time(1, 0))
    assert a.minutos_na_janela(datetime(2026, 9, 26, 0, 30), time(23, 0), time(1, 0)) == (90, 120)
    assert a.minutos_na_janela(datetime(2026, 9, 26, 1, 30), time(23, 0), time(1, 0)) is None
    d = decidir(p=p, agora_local=datetime(2026, 9, 26, 0, 59))
    assert d.instalar


def test_espalhamento_fixo_por_agente_e_limitado() -> None:
    esperas = {a.espalhamento_min(f"ag_{i}", 180) for i in range(500)}
    assert max(esperas) < 60 and len(esperas) > 30  # espalha, e dentro da primeira hora
    assert a.espalhamento_min("ag_x", 180) == a.espalhamento_min("ag_x", 180)
    assert a.espalhamento_min("ag_x", 5) < 5  # janela curta: cabe nela
    tardio = next(f"ag_{i}" for i in range(1000) if a.espalhamento_min(f"ag_{i}", 180) > 40)
    d = decidir(agente_id=tardio, agora_local=datetime(2026, 9, 26, 2, 10))
    assert not d.instalar and d.estado.estado == a.AGUARDANDO


def test_atualizar_agora_ignora_a_janela_mas_nao_o_ocioso() -> None:
    fora = datetime(2026, 9, 26, 14, 0)
    d = decidir(p=pedido(agora="2026-09-26T13:59:00Z"), agora_local=fora)
    assert d.instalar and d.estado.pedido_tratado == "2026-09-26T13:59:00Z"
    ocupado = decidir(p=pedido(agora="2026-09-26T13:59:00Z"), agora_local=fora, ocupado="há aplicação")
    assert not ocupado.instalar and ocupado.estado.estado == a.OCUPADO
    # Pedido já tratado não vale de novo: volta a respeitar a janela.
    tratado = a.Estado(pedido_tratado="2026-09-26T13:59:00Z")
    assert not decidir(tratado, pedido(agora="2026-09-26T13:59:00Z"), agora_local=fora).instalar


def test_ocupado_na_janela_nao_instala() -> None:
    d = decidir(ocupado="há acesso web aberto pelo túnel do NOC")
    assert not d.instalar and d.estado.estado == a.OCUPADO and "túnel" in d.estado.detalhe


# --- Tentativas e desfecho ------------------------------------------------------------------------


def test_instalando_espera_o_prazo_e_depois_e_falha() -> None:
    instalando = a.Estado(a.INSTALANDO, "2.14.0", tentativas=1, ultima_tentativa=AGORA - timedelta(minutes=5))
    assert decidir(instalando).estado.estado == a.INSTALANDO
    vencido = a.Estado(a.INSTALANDO, "2.14.0", tentativas=1, ultima_tentativa=AGORA - timedelta(minutes=25))
    d = decidir(vencido)
    assert not d.instalar and d.estado.estado == a.FALHOU


def test_instalando_e_a_versao_nova_rodando_vira_em_dia() -> None:
    instalando = a.Estado(a.INSTALANDO, "2.14.0", tentativas=1, ultima_tentativa=AGORA - timedelta(minutes=2))
    d = decidir(instalando, versao_instalada="2.14.0")
    assert d.estado.estado == a.EM_DIA and d.estado.tentativas == 0


def test_uma_tentativa_por_hora_e_tres_no_maximo() -> None:
    falhou = a.Estado(
        a.FALHOU, "2.14.0", "download", tentativas=1, ultima_tentativa=AGORA - timedelta(minutes=30)
    )
    assert not decidir(falhou).instalar
    passou = a.Estado(
        a.FALHOU, "2.14.0", "download", tentativas=1, ultima_tentativa=AGORA - timedelta(minutes=61)
    )
    assert decidir(passou).instalar
    esgotou = a.Estado(
        a.FALHOU, "2.14.0", "download", tentativas=3, ultima_tentativa=AGORA - timedelta(hours=5)
    )
    d = decidir(esgotou)
    assert not d.instalar and d.estado.estado == a.FALHOU and d.estado.detalhe == "download"


def test_versao_que_voltou_nao_se_repete_sozinha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import middleware_monitor.desktop as desktop

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop, "get_data_dir", lambda: tmp_path)
    (tmp_path / "update_result.txt").write_text("voltou 2.14.0 a versao nova nao respondeu em 150 s\n")
    instalando = a.Estado(a.INSTALANDO, "2.14.0", tentativas=1, ultima_tentativa=AGORA - timedelta(hours=2))
    depois = a._resultado_do_windows(instalando)
    assert depois.estado == a.FALHOU and depois.tentativas == a.MAXIMO_DE_TENTATIVAS
    assert not decidir(depois).instalar  # na janela, e mesmo assim não tenta
    assert decidir(depois, pedido(agora="2026-09-26T03:29:00Z")).instalar  # o botão força


def test_atualizar_agora_zera_as_tentativas() -> None:
    esgotou = a.Estado(
        a.FALHOU, "2.14.0", "download", tentativas=3, ultima_tentativa=AGORA - timedelta(minutes=5)
    )
    d = decidir(esgotou, pedido(agora="2026-09-26T03:29:00Z"))
    assert d.instalar and d.estado.tentativas == 1


def test_versao_nova_no_noc_zera_as_tentativas() -> None:
    esgotou = a.Estado(
        a.FALHOU, "2.14.0", "download", tentativas=3, ultima_tentativa=AGORA - timedelta(hours=2)
    )
    assert decidir(esgotou, pedido(versao="2.14.1")).instalar


def test_sem_release_so_consulta_de_novo_depois_de_uma_hora() -> None:
    sem = a.Estado(a.SEM_RELEASE, "2.14.0", em=AGORA - timedelta(minutes=10))
    assert not decidir(sem).instalar
    assert decidir(a.Estado(a.SEM_RELEASE, "2.14.0", em=AGORA - timedelta(minutes=61))).instalar


# --- Heartbeat --------------------------------------------------------------------------------------


def test_pedido_do_heartbeat() -> None:
    p = a.Pedido.do_heartbeat(
        {
            "versaoDesejada": "2.14.0",
            "janela": {"inicio": "23:30", "fim": "01:00"},
            "atualizarAgoraPedidoEm": None,
        }
    )
    assert p == a.Pedido("2.14.0", time(23, 30), time(1, 0), None)
    # NOC antigo não manda o campo: nada muda, e o estado não vai no heartbeat.
    assert a.Pedido.do_heartbeat(None) is None
    # Janela fora de forma cai no padrão, em vez de travar a atualização.
    assert a.Pedido.do_heartbeat({"versaoDesejada": "2.14.0", "janela": {"inicio": "25:00"}}) == a.Pedido(
        "2.14.0", time(2, 0), time(5, 0), None
    )


def test_estado_para_o_noc_respeita_os_limites_do_contrato() -> None:
    corpo = a.Estado(a.FALHOU, "2.14.0", "x" * 500, em=AGORA, tentativas=150).para_o_noc()
    assert corpo["estado"] == "FALHOU" and len(corpo["detalhe"]) == 300 and corpo["tentativas"] == 99
    assert corpo["em"] == "2026-09-26T03:30:00Z"


async def test_ciclo_sem_anuncio_do_noc_nao_manda_nada() -> None:
    assert await a.ciclo(None, agente_id="ag_1") is None


async def test_ciclo_grava_e_devolve_o_estado(db, monkeypatch) -> None:
    monkeypatch.setattr(a, "ocupacao", lambda *_: None)
    corpo = await a.ciclo(
        {
            "versaoDesejada": "0.0.1",
            "janela": {"inicio": "02:00", "fim": "05:00"},
            "atualizarAgoraPedidoEm": None,
        },
        agente_id="ag_1",
    )
    assert corpo is not None and corpo["estado"] == a.ACIMA
    assert a.carregar(db).estado == a.ACIMA


# --- O ajudante do Windows, de verdade ----------------------------------------------------------
#
# O ajudante sobe por ``disparar_ajudante`` — o mesmo caminho da produção. Até a 2.14.1 este
# teste rodava o ``.bat`` com ``subprocess.run`` dentro do console do pytest, e com console o
# ``timeout`` espera; destacado, na produção, falhava na hora e abria uma janela por comando.


def test_ler_resultado(tmp_path: Path) -> None:
    (tmp_path / "update_result.txt").write_text("voltou 2.14.0 a versao nova nao respondeu em 150 s\n")
    assert ler_resultado(tmp_path) == ("voltou", "2.14.0", "a versao nova nao respondeu em 150 s")
    assert ler_resultado(tmp_path) is None  # lido uma vez só


def test_o_ajudante_nao_chama_programa_de_console(tmp_path: Path) -> None:
    script = script_do_ajudante(
        novo=tmp_path / "n.exe",
        atual=tmp_path / "a.exe",
        alvo="2.14.2",
        porta=8080,
        resultado=tmp_path / "r.txt",
        trava=tmp_path / "t.lock",
    ).lower()
    for proibido in ("cmd", "tasklist", "timeout /t", "find ", "taskkill", "powershell ", "chcp"):
        assert proibido not in script, proibido


def test_caminho_com_aspas_simples_nao_quebra_o_script(tmp_path: Path) -> None:
    script = script_do_ajudante(
        novo=Path("C:/Users/D'Avila/tmp/n.exe"),
        atual=Path("C:/Users/D'Avila/app/a.exe"),
        alvo="2.14.2",
        porta=8080,
        resultado=tmp_path / "r.txt",
        trava=tmp_path / "t.lock",
    )
    assert "D''Avila" in script


def test_uma_troca_por_vez(tmp_path: Path) -> None:
    trava = tmp_path / "update.lock"
    st.tomar_trava(trava)
    with pytest.raises(st.UpdateError, match="em andamento"):
        st.tomar_trava(trava)


def test_trava_esquecida_vence_sozinha(tmp_path: Path) -> None:
    import os

    trava = tmp_path / "update.lock"
    trava.write_text("1")
    velha = relogio.time() - st.TRAVA_VENCE_S - 1
    os.utime(trava, (velha, velha))
    st.tomar_trava(trava)  # não levanta


class _Saude(BaseHTTPRequestHandler):
    versao = "2.14.0"

    def do_GET(self) -> None:
        corpo = f'{{"status":"ok","version":"{self.versao}"}}'.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *_: object) -> None:
        pass


def _janelas_de_console() -> set[int]:
    """Janelas de console visíveis agora (conhost clássico e Windows Terminal)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    classes = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}
    achadas: set[int] = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)  # type: ignore[attr-defined]
    def cada(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            nome = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, nome, 256)
            if nome.value in classes:
                achadas.add(int(hwnd))
        return True

    user32.EnumWindows(cada, 0)
    return achadas


def _rodar_ajudante(tmp_path: Path, *, com_saude: bool, espera: int) -> tuple[str, bytes, float, set[int]]:
    sistema = Path("C:/Windows/System32")
    atual = tmp_path / "app" / "FakeMonitor.exe"
    novo = tmp_path / "tmp" / "novo.exe"
    atual.parent.mkdir()
    novo.parent.mkdir()
    # Executável de janela (GUI), como o middleware (console=False no .spec) e que sai na
    # hora: um de console abriria a própria janela e esconderia uma do ajudante.
    gui = (sistema / "rundll32.exe").read_bytes()
    atual.write_bytes(gui + b"\x00antigo")
    novo.write_bytes(gui + b"\x00novo")
    antigo = atual.read_bytes()
    trava = tmp_path / "tmp" / st.TRAVA
    st.tomar_trava(trava)
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Saude)
    porta = servidor.server_address[1]
    if com_saude:
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
    else:
        servidor.server_close()
    original = st.ESPERA_DA_SAUDE_S
    st.ESPERA_DA_SAUDE_S = espera
    antes = _janelas_de_console()
    novas: set[int] = set()
    inicio = relogio.monotonic()
    try:
        ps1 = tmp_path / st.AJUDANTE
        ps1.write_text(
            script_do_ajudante(
                novo=novo,
                atual=atual,
                alvo="2.14.0",
                porta=porta,
                resultado=tmp_path / "update_result.txt",
                trava=trava,
            ),
            encoding="utf-8-sig",
        )
        processo = st.disparar_ajudante(ps1)
        while processo.poll() is None and relogio.monotonic() - inicio < espera + 60:
            novas |= _janelas_de_console() - antes
            relogio.sleep(0.2)
        processo.wait(timeout=5)
    finally:
        st.ESPERA_DA_SAUDE_S = original
        if com_saude:
            servidor.shutdown()
    duracao = relogio.monotonic() - inicio
    resultado = (tmp_path / "update_result.txt").read_text(encoding="utf-8").strip()
    assert not trava.exists()  # a trava sai junto com o ajudante
    assert not (tmp_path / st.AJUDANTE).exists()  # e o ajudante se apaga
    return resultado, atual.read_bytes() if atual.read_bytes() != antigo else b"ANTIGO", duracao, novas


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="o ajudante é do Windows")
def test_ajudante_troca_quando_a_versao_nova_responde(tmp_path: Path) -> None:
    resultado, conteudo, _, janelas = _rodar_ajudante(tmp_path, com_saude=True, espera=20)
    assert resultado == "ok 2.14.0"
    assert conteudo != b"ANTIGO"  # o executável novo ficou
    assert (tmp_path / "app" / "FakeMonitor.exe.bak").exists()
    assert janelas == set()  # nenhuma janela de console


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="o ajudante é do Windows")
def test_ajudante_volta_quando_a_versao_nova_nao_responde(tmp_path: Path) -> None:
    resultado, conteudo, duracao, janelas = _rodar_ajudante(tmp_path, com_saude=False, espera=10)
    assert resultado.startswith("voltou 2.14.0")
    assert conteudo == b"ANTIGO"  # o executável anterior voltou
    # A espera espera de verdade: o .bat destacado girava sem pausa (timeout saía com 125).
    assert duracao >= 10
    assert janelas == set()
