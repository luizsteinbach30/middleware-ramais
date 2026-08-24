"""Testes da ferramenta de medição (`scripts/perf_baseline.py`).

Duas das regras dela são de segurança, não de conveniência, e falham em
silêncio: se a cópia deixar de desabilitar broker e servidor, a medição conecta
no EMQX do cliente com o `client_id` da produção; se a janela deixar de ser
ancorada no dado, a medição varre janela vazia e devolve número bonito medindo
nada. As duas ficam presas aqui.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

CAMINHO = Path(__file__).resolve().parents[2] / "scripts" / "perf_baseline.py"


def _modulo():
    spec = importlib.util.spec_from_file_location("perf_baseline", CAMINHO)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["perf_baseline"] = mod
    spec.loader.exec_module(mod)
    return mod


perf = _modulo()


def _banco_sintetico(destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(destino)
    con.executescript(
        """
        CREATE TABLE mqtt_brokers (id INTEGER PRIMARY KEY, enabled INTEGER);
        CREATE TABLE uscall_servers (id INTEGER PRIMARY KEY, enabled INTEGER);
        CREATE TABLE mqtt_messages (id INTEGER PRIMARY KEY, payload TEXT, received_at TEXT);
        CREATE INDEX ix_msg_received ON mqtt_messages (received_at);
        CREATE TABLE devices (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE extension_environments (id TEXT PRIMARY KEY);
        INSERT INTO mqtt_brokers VALUES (1, 1);
        INSERT INTO uscall_servers VALUES (1, 1);
        INSERT INTO devices VALUES (1, 'fone');
        """
    )
    con.executemany(
        "INSERT INTO mqtt_messages (payload, received_at) VALUES (?, ?)",
        [("x" * 512, f"2026-08-24 10:{i % 60:02d}:00") for i in range(500)],
    )
    con.commit()
    con.close()
    return destino


def test_copia_desabilita_broker_e_servidor(tmp_path: Path) -> None:
    origem = _banco_sintetico(tmp_path / "origem" / "app.db")
    copia = perf.preparar_copia(origem, tmp_path / "trabalho")

    con = sqlite3.connect(copia)
    assert con.execute("SELECT enabled FROM mqtt_brokers").fetchone()[0] == 0
    assert con.execute("SELECT enabled FROM uscall_servers").fetchone()[0] == 0
    con.close()

    # O banco da instalação não pode ter sido tocado.
    con = sqlite3.connect(origem)
    assert con.execute("SELECT enabled FROM mqtt_brokers").fetchone()[0] == 1
    con.close()


def test_copia_nao_leva_o_shm(tmp_path: Path) -> None:
    """Um `-shm` de outro processo faz o SQLite ignorar o WAL da cópia.

    O sintoma é traiçoeiro: o banco abre normalmente e mostra só
    `alembic_version`, como se estivesse vazio.
    """
    origem = _banco_sintetico(tmp_path / "origem" / "app.db")
    origem.with_name("app.db-shm").write_bytes(b"\x00" * 32768)

    copia = perf.preparar_copia(origem, tmp_path / "trabalho")

    assert not copia.with_name(copia.name + "-shm").exists()
    con = sqlite3.connect(copia)
    tabelas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "mqtt_messages" in tabelas


@pytest.mark.parametrize(
    ("horas", "esperado_delta_segundos"),
    [(0.25, 900), (24, 86400), (24 * 7, 604800)],
)
def test_janela_ancora_no_dado_mais_novo(horas: float, esperado_delta_segundos: int) -> None:
    fim = datetime(2026, 8, 24, 15, 29, 56)
    qs = perf._janela(fim, horas)

    assert "since=" in qs and "until=" in qs
    partes = dict(p.split("=", 1) for p in qs.split("&"))
    from urllib.parse import unquote

    inicio = datetime.fromisoformat(unquote(partes["since"]))
    assert datetime.fromisoformat(unquote(partes["until"])) == fim
    assert (fim - inicio).total_seconds() == esperado_delta_segundos


def test_janela_sem_dado_cai_no_last() -> None:
    """Banco sem mensagem nenhuma: não há onde ancorar, usa a janela relativa."""
    assert perf._janela(None, 24) == "last=24h"


def test_medir_banco_ordena_pela_maior_tabela(tmp_path: Path) -> None:
    origem = _banco_sintetico(tmp_path / "origem" / "app.db")
    copia = perf.preparar_copia(origem, tmp_path / "trabalho")

    resultado = perf.medir_banco(copia)

    assert resultado["arquivo_bytes"] > 0
    maior = resultado["tabelas"][0]
    assert maior["tabela"] == "mqtt_messages"
    assert maior["linhas"] == 500
    assert maior["tabela_bytes"] > 0
    assert maior["indices_bytes"] > 0  # ix_msg_received cobrado à parte
    # A cópia descartável usada para medir por diferença não pode sobrar.
    assert not copia.with_name("sizing.db").exists()


def test_relatorio_sai_em_markdown() -> None:
    dados = {
        "gerado_em": "2026-08-24T17:00:00-03:00",
        "banco_origem": "app.db",
        "jobs": [{"job": "retention_daily", "ms": 18.0, "nota": "poda", "erro": ""}],
        "telas": [
            {
                "tela": "Dashboard",
                "nota": "",
                "total_p50_ms": 20.0,
                "total_bytes": 13312,
                "requisicoes": [
                    {
                        "caminho": "/",
                        "status": 200,
                        "bytes": 13312,
                        "p50_ms": 11.0,
                        "p95_ms": 12.0,
                        "max_ms": 12.0,
                    }
                ],
            }
        ],
    }

    texto = perf.render_markdown(dados)

    assert "## Jobs" in texto
    assert "## Telas" in texto
    assert "retention_daily" in texto
    assert "Dashboard" in texto
