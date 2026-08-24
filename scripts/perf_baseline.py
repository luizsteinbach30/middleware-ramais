#!/usr/bin/env python
"""Medição-base de desempenho — roadmap §15.6, etapa 1 ("medir, não mexer").

Produz números repetíveis para as três perguntas que a §15.6 faz antes de
qualquer mudança de arquitetura: **quanto pesa cada tabela**, **quanto demora
cada job** e **quanto demora cada tela**. Rodar de novo depois de uma mudança dá
a comparação — é para isso que a ferramenta é versionada, e não um script
descartável.

Regras de segurança da medição, todas com motivo:

* tudo roda sobre uma **cópia** do banco, em diretório temporário — a medição
  nunca escreve no banco da instalação (a seção de jobs apaga linhas de
  propósito, ao medir a retenção);
* na cópia, brokers MQTT e servidores USCall são **desabilitados** — sem isso o
  processo de medição conecta no EMQX do cliente com o mesmo ``client_id`` da
  produção e briga com a sessão durável;
* as telas são medidas **em processo** (ASGI direto, sem uvicorn e sem rede) e
  sem o ciclo de vida da aplicação, ou seja, sem scheduler e sem coletor MQTT
  concorrendo. O número é o custo de servidor puro — o piso. Contenção com a
  ingestão é fator separado, e a §15.6 já suspeita dela.

Uso::

    python scripts/perf_baseline.py                       # tudo, relatório no stdout
    python scripts/perf_baseline.py --out reports/perf.md
    python scripts/perf_baseline.py --secao banco --secao telas
    python scripts/perf_baseline.py --json reports/perf.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

# Senha usada só dentro da cópia temporária, para conseguir sessão autenticada
# sem depender de saber a senha real do admin da instalação.
SENHA_MEDICAO = "medicao-perf-nao-usar"

SECOES = ("banco", "jobs", "telas")


# --------------------------------------------------------------------------- #
# preparação da cópia
# --------------------------------------------------------------------------- #
def preparar_copia(origem: Path, data_dir: Path) -> Path:
    """Copia o banco para um ``APP_DATA_DIR`` temporário pronto para medir."""
    (data_dir / "db").mkdir(parents=True, exist_ok=True)
    (data_dir / "backups").mkdir(parents=True, exist_ok=True)
    (data_dir / "tmp").mkdir(parents=True, exist_ok=True)
    alvo = data_dir / "db" / "app.db"
    shutil.copy2(origem, alvo)
    wal = origem.with_name(origem.name + "-wal")
    if wal.exists():
        shutil.copy2(wal, alvo.with_name(alvo.name + "-wal"))
    # O ``-shm`` NÃO é copiado de propósito: um -shm escrito por outro processo
    # faz o SQLite ignorar o WAL, e o banco aparece quase vazio (só
    # ``alembic_version``). Sem o -shm o WAL é recuperado normalmente.
    con = sqlite3.connect(alvo)
    con.isolation_level = None
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for tabela in ("mqtt_brokers", "uscall_servers"):
        try:
            con.execute(f"UPDATE {tabela} SET enabled=0")  # noqa: S608 - nome fixo
        except sqlite3.OperationalError:
            pass
    con.close()
    return alvo


def admin_de_medicao(banco: Path) -> str | None:
    """Troca a senha do primeiro usuário **na cópia** e devolve o login."""
    from middleware_monitor.core.security import hash_password

    con = sqlite3.connect(banco)
    row = con.execute("SELECT id, username FROM users ORDER BY id LIMIT 1").fetchone()
    if row is None:
        con.close()
        return None
    con.execute(
        "UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
        (hash_password(SENHA_MEDICAO), row[0]),
    )
    con.commit()
    con.close()
    return str(row[1])


# --------------------------------------------------------------------------- #
# seção: banco
# --------------------------------------------------------------------------- #
def _tamanho_paginas(con: sqlite3.Connection) -> int:
    page_size = int(con.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(con.execute("PRAGMA page_count").fetchone()[0])
    return page_size * page_count


def medir_banco(banco: Path) -> dict[str, Any]:
    """Tamanho real por tabela e por índice.

    O SQLite embutido no Python não traz a tabela virtual ``dbstat``, então o
    tamanho é medido por diferença: numa cópia descartável, derrubam-se os
    índices da tabela, ``VACUUM``, derruba-se a tabela, ``VACUUM`` — cada delta é
    o espaço que aquilo ocupava de fato. Índice implícito
    (``sqlite_autoindex_*``) não pode ser derrubado sozinho e entra no total da
    tabela.
    """
    con = sqlite3.connect(banco)
    con.isolation_level = None
    page_size = int(con.execute("PRAGMA page_size").fetchone()[0])
    wal = banco.with_name(banco.name + "-wal")
    resumo: dict[str, Any] = {
        "arquivo_bytes": banco.stat().st_size,
        "wal_bytes": wal.stat().st_size if wal.exists() else 0,
        "page_size": page_size,
        "page_count": int(con.execute("PRAGMA page_count").fetchone()[0]),
        "freelist_count": int(con.execute("PRAGMA freelist_count").fetchone()[0]),
    }
    tabelas = [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]
    linhas = {
        t: int(con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])  # noqa: S608
        for t in tabelas
    }
    indices = {
        t: [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? "
                "AND name NOT LIKE 'sqlite_autoindex_%'",
                (t,),
            ).fetchall()
        ]
        for t in tabelas
    }
    con.close()

    # A medição por diferença destrói o banco: faz numa cópia descartável.
    sizing = banco.with_name("sizing.db")
    shutil.copy2(banco, sizing)
    con = sqlite3.connect(sizing)
    con.isolation_level = None
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("VACUUM")
    detalhe: list[dict[str, Any]] = []
    for t in tabelas:
        antes = _tamanho_paginas(con)
        for idx in indices[t]:
            con.execute(f'DROP INDEX "{idx}"')
        if indices[t]:
            con.execute("VACUUM")
        meio = _tamanho_paginas(con)
        con.execute(f'DROP TABLE "{t}"')
        con.execute("VACUUM")
        depois = _tamanho_paginas(con)
        detalhe.append(
            {
                "tabela": t,
                "linhas": linhas[t],
                "tabela_bytes": max(0, meio - depois),
                "indices_bytes": max(0, antes - meio),
                "total_bytes": max(0, antes - depois),
                "indices": indices[t],
            }
        )
    con.close()
    sizing.unlink(missing_ok=True)
    detalhe.sort(key=lambda d: -int(d["total_bytes"]))
    resumo["tabelas"] = detalhe
    return resumo


# --------------------------------------------------------------------------- #
# seção: jobs
# --------------------------------------------------------------------------- #
async def _cronometrar(nome: str, fn: Any, nota: str = "") -> dict[str, Any]:
    inicio = time.perf_counter()
    erro = ""
    try:
        resultado = fn()
        if asyncio.iscoroutine(resultado):
            await resultado
    except Exception as exc:  # a medição registra a falha, não morre por ela
        erro = f"{type(exc).__name__}: {exc}"
    return {
        "job": nome,
        "ms": round((time.perf_counter() - inicio) * 1000, 1),
        "nota": nota,
        "erro": erro,
    }


def _contar(db: Any, tabela: str) -> int:
    from sqlalchemy import text

    return int(db.execute(text(f"SELECT COUNT(*) FROM {tabela}")).scalar() or 0)  # noqa: S608


async def medir_jobs(banco: Path) -> list[dict[str, Any]]:
    """Duração dos jobs que dependem do banco, na ordem em que podem rodar.

    Fora daqui, de propósito: ``collect_extensions`` (fala com a API USCall) e
    ``monitor_devices`` (dispara ping em ~1930 IPs do cliente). Os dois já
    gravam ``duration_ms`` no log — o número real de produção vem de lá, e não
    de uma varredura de rede disparada por uma medição.
    """
    from middleware_monitor.core.db import init_engine, session_factory

    init_engine()
    from middleware_monitor.domain.backup.snapshot import create_snapshot
    from middleware_monitor.jobs.rebuild_calls import run_daily_stats, run_rebuild_calls
    from middleware_monitor.jobs.retention import run_retention

    saida: list[dict[str, Any]] = []

    # 1) Como roda em produção: a cada 60 s, só o que chegou desde a última vez.
    saida.append(
        await _cronometrar(
            "rebuild_calls (incremental)",
            run_rebuild_calls,
            "como roda em produção, a cada 60 s",
        )
    )

    # 2) Pior caso: reprocessar tudo o que está na retenção. Só é seguro porque
    #    é cópia — em produção a marca d'água nunca anda para trás (§15.2).
    con = sqlite3.connect(banco)
    transicoes = int(con.execute("SELECT COUNT(*) FROM extension_status_events").fetchone()[0])
    con.execute("DELETE FROM extension_calls")
    con.commit()
    con.close()
    saida.append(
        await _cronometrar(
            "rebuild_calls (completo)",
            run_rebuild_calls,
            f"reprocessa {transicoes:,} transições — pior caso, não acontece em produção",
        )
    )

    saida.append(await _cronometrar("daily_stats", run_daily_stats, "recalcula ontem e hoje"))

    saida.append(
        await _cronometrar(
            "backup snapshot",
            lambda: create_snapshot(label="perf"),
            "VACUUM INTO + gzip do banco inteiro",
        )
    )

    # Retenção por último: apaga linhas, então mudaria as medidas acima.
    podadas = ("mqtt_messages", "extension_status_events", "device_pings")
    antes_arquivo = banco.stat().st_size
    with session_factory() as db:
        antes = {t: _contar(db, t) for t in podadas}
    registro = await _cronometrar("retention_daily", run_retention, "poda diária")
    with session_factory() as db:
        depois = {t: _contar(db, t) for t in podadas}
    registro["apagou"] = {t: antes[t] - depois[t] for t in podadas}
    registro["arquivo_antes_bytes"] = antes_arquivo
    registro["arquivo_depois_bytes"] = banco.stat().st_size
    saida.append(registro)
    return saida


# --------------------------------------------------------------------------- #
# seção: telas
# --------------------------------------------------------------------------- #
def _janela(fim: datetime | None, horas: float) -> str:
    """Query string ``since``/``until`` ancorada no dado mais novo do banco.

    Sem isto a medição mente para o lado otimista: o banco é um retrato, o
    relógio anda, e ``last=15m`` acaba varrendo uma janela **vazia** — a tela de
    ledger responderia em 3 ms medindo nada. Ancorar no último ``received_at``
    mantém a janela com o mesmo volume que o operador vê em produção.
    """
    if fim is None:
        return f"last={int(horas)}h"
    inicio = fim - timedelta(hours=horas)
    return f"since={quote(inicio.isoformat())}&until={quote(fim.isoformat())}"


def _alvos(banco: Path) -> list[dict[str, Any]]:
    """Cada tela e as chamadas que ela dispara ao carregar.

    A lista sai dos módulos JS de cada página (``web/static/js/pages``): medir
    só o HTML mediria a casca, que é sempre rápida — o custo está nas APIs que a
    tela chama no load.
    """
    con = sqlite3.connect(banco)
    linha_device = con.execute("SELECT id FROM devices ORDER BY id LIMIT 1").fetchone()
    linha_env = con.execute(
        "SELECT id FROM extension_environments ORDER BY id LIMIT 1"
    ).fetchone()
    linha_fim = con.execute("SELECT MAX(received_at) FROM mqtt_messages").fetchone()
    con.close()
    device_id = linha_device[0] if linha_device else None
    env_id = linha_env[0] if linha_env else None
    fim = datetime.fromisoformat(linha_fim[0]) if linha_fim and linha_fim[0] else None
    j15m, j24h, j7d = _janela(fim, 0.25), _janela(fim, 24), _janela(fim, 24 * 7)

    telas: list[dict[str, Any]] = [
        {
            "tela": "Dashboard",
            "doc": "/",
            "apis": [
                "/api/dashboard/summary",
                "/api/dashboard/timeseries?window_hours=24",
                "/api/system/version",
            ],
        },
        {
            "tela": "Ramais (lista)",
            "doc": "/devices",
            "apis": [
                "/api/devices?page=1&size=50",
                "/api/devices/summary",
                "/api/extension-configurator/environments",
                "/api/extension-configurator/phone-models",
            ],
        },
        {
            "tela": "Ramais (filtro de faixa)",
            "doc": None,
            "apis": ["/api/devices?page=1&size=50&ip_from=0.0.0.0&ip_to=255.255.255.255"],
            "nota": "filtro de faixa pagina em memória (devices/repository.py)",
        },
        {
            "tela": "Painel ao vivo",
            "doc": "/mqtt-painel",
            "apis": ["/api/mqtt/live"],
            "nota": (
                "recarrega a cada 2,5 s — **medida por baixo**: o estado vem da "
                "memória do coletor, que aqui não está rodando"
            ),
        },
        {
            "tela": "Chamadas (24 h)",
            "doc": "/mqtt-chamadas",
            "apis": [f"/api/mqtt/calls?{j24h}&limit=100&offset=0"],
        },
        {
            "tela": "Chamadas (7 dias)",
            "doc": None,
            "apis": [f"/api/mqtt/calls?{j7d}&limit=100&offset=0"],
            "nota": "janela mais larga que a tela oferece",
        },
        {
            "tela": "Mensagens (ledger, 15 min)",
            "doc": "/mqtt-messages",
            "apis": [
                f"/api/mqtt/messages?{j15m}&limit=100",
                f"/api/mqtt/coverage?{j15m}",
                "/api/mqtt/status",
            ],
        },
        {
            "tela": "Mensagens (ledger, 7 dias)",
            "doc": None,
            "apis": [
                f"/api/mqtt/messages?{j7d}&limit=100",
                f"/api/mqtt/coverage?{j7d}",
            ],
            "nota": "janela mais larga que a tela oferece — varre o ledger inteiro",
        },
        {"tela": "Coletas", "doc": "/collections", "apis": ["/api/collections"]},
        {"tela": "Logs", "doc": "/logs", "apis": ["/api/logs?page=1&size=100"]},
        {"tela": "Webhooks", "doc": "/webhook-logs", "apis": ["/api/webhook-events"]},
        {
            "tela": "Configurador (lista)",
            "doc": "/extension-configurator/environments",
            "apis": [
                "/api/extension-configurator/environments",
                "/api/extension-configurator/phone-models",
            ],
        },
        {
            "tela": "Configurador (execuções)",
            "doc": "/extension-configurator/runs",
            "apis": ["/api/extension-configurator/runs"],
        },
        {
            "tela": "Configurações",
            "doc": "/config",
            "apis": [
                "/api/config",
                "/api/config/uscall-servers",
                "/api/config/timezones",
                "/api/branding/status",
                "/api/mqtt/brokers",
            ],
        },
        {
            "tela": "Backup",
            "doc": "/system/backup",
            "apis": ["/api/backup/files", "/api/backup/settings", "/api/backup/restore"],
        },
        {
            "tela": "Updates",
            "doc": "/system/updates",
            "apis": ["/api/system/update-settings", "/api/system/update-history"],
            "nota": "a verificação de update em si fala com o GitHub e fica fora",
        },
    ]
    if device_id is not None:
        telas.append(
            {
                "tela": "Detalhe do ramal",
                "doc": f"/devices/{device_id}",
                "apis": [
                    f"/api/devices/{device_id}",
                    f"/api/devices/{device_id}/history?window=24h",
                    f"/api/devices/{device_id}/pings?limit=20",
                    f"/api/devices/{device_id}/reapply-events?limit=10",
                    f"/api/devices/{device_id}/link-environments",
                ],
            }
        )
    if env_id is not None:
        telas.append(
            {
                "tela": "Configurador (detalhe do ambiente)",
                "doc": f"/extension-configurator/environments/{env_id}",
                "apis": [
                    f"/api/extension-configurator/environments/{env_id}",
                    f"/api/extension-configurator/environments/{env_id}/capabilities",
                ],
            }
        )
    return telas


async def medir_telas(banco: Path, usuario: str, repeticoes: int) -> list[dict[str, Any]]:
    import httpx

    from middleware_monitor.app import get_app
    from middleware_monitor.core.db import init_engine

    init_engine()
    app = get_app()
    transporte = httpx.ASGITransport(app=app)
    resultados: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        transport=transporte, base_url="http://medicao", timeout=300.0
    ) as cli:
        login = await cli.post(
            "/api/auth/login", json={"username": usuario, "password": SENHA_MEDICAO}
        )
        if login.status_code != 200:
            raise RuntimeError(f"login da medição falhou: {login.status_code} {login.text}")

        for tela in _alvos(banco):
            caminhos = ([tela["doc"]] if tela.get("doc") else []) + list(tela["apis"])
            medidas: list[dict[str, Any]] = []
            for caminho in caminhos:
                amostras: list[float] = []
                status = 0
                tamanho = 0
                for _ in range(repeticoes):
                    inicio = time.perf_counter()
                    resp = await cli.get(caminho, follow_redirects=False)
                    amostras.append((time.perf_counter() - inicio) * 1000)
                    status = resp.status_code
                    tamanho = len(resp.content)
                amostras.sort()
                p95 = amostras[min(len(amostras) - 1, int(len(amostras) * 0.95))]
                medidas.append(
                    {
                        "caminho": caminho,
                        "status": status,
                        "bytes": tamanho,
                        "p50_ms": round(statistics.median(amostras), 1),
                        "p95_ms": round(p95, 1),
                        "max_ms": round(amostras[-1], 1),
                    }
                )
            resultados.append(
                {
                    "tela": tela["tela"],
                    "nota": tela.get("nota", ""),
                    "total_p50_ms": round(sum(float(m["p50_ms"]) for m in medidas), 1),
                    "total_bytes": sum(int(m["bytes"]) for m in medidas),
                    "requisicoes": medidas,
                }
            )
    resultados.sort(key=lambda r: -float(r["total_p50_ms"]))
    return resultados


# --------------------------------------------------------------------------- #
# relatório
# --------------------------------------------------------------------------- #
def _mb(n: float) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} KB"


def render_markdown(dados: dict[str, Any]) -> str:
    linhas: list[str] = []
    add = linhas.append
    add("# Medição-base de desempenho")
    add("")
    add(f"Gerado por `scripts/perf_baseline.py` em {dados['gerado_em']}.")
    add(f"Banco medido: `{dados['banco_origem']}` (cópia em diretório temporário).")
    add("")

    banco = dados.get("banco")
    if banco:
        add("## Banco")
        add("")
        add(
            f"Arquivo **{_mb(banco['arquivo_bytes'])}** + WAL {_mb(banco['wal_bytes'])} · "
            f"página {banco['page_size']} B · {banco['page_count']} páginas · "
            f"{banco['freelist_count']} livres."
        )
        add("")
        add("| Tabela | Linhas | Dados | Índices | Total | % |")
        add("|---|---:|---:|---:|---:|---:|")
        total = sum(int(t["total_bytes"]) for t in banco["tabelas"]) or 1
        for t in banco["tabelas"]:
            if not t["linhas"] and not t["total_bytes"]:
                continue
            add(
                f"| `{t['tabela']}` | {t['linhas']:,} | {_mb(t['tabela_bytes'])} | "
                f"{_mb(t['indices_bytes'])} | {_mb(t['total_bytes'])} | "
                f"{100 * int(t['total_bytes']) / total:.1f}% |"
            )
        add("")

    jobs = dados.get("jobs")
    if jobs:
        add("## Jobs")
        add("")
        add("| Job | Duração | Observação |")
        add("|---|---:|---|")
        for j in jobs:
            nota = j.get("nota", "")
            if j.get("apagou"):
                apagou = ", ".join(f"{k}: {v:,}" for k, v in j["apagou"].items() if v)
                nota = f"{nota} — apagou {apagou or 'nada'}"
            if j.get("erro"):
                nota = f"{nota} — **falhou**: {j['erro']}"
            add(f"| `{j['job']}` | {j['ms']:,.0f} ms | {nota} |")
        add("")

    telas = dados.get("telas")
    if telas:
        add("## Telas")
        add("")
        add("Tempo de servidor em processo (sem rede, sem scheduler, sem coletor).")
        add("")
        add("| Tela | Total p50 | Payload | Requisição mais cara |")
        add("|---|---:|---:|---|")
        for t in telas:
            pior = max(t["requisicoes"], key=lambda r: float(r["p50_ms"]))
            add(
                f"| {t['tela']} | {t['total_p50_ms']:,.0f} ms | {_mb(t['total_bytes'])} | "
                f"`{pior['caminho']}` {pior['p50_ms']:,.0f} ms |"
            )
        add("")
        add("<details><summary>Requisição a requisição</summary>")
        add("")
        add("| Tela | Requisição | Status | p50 | p95 | máx | Bytes |")
        add("|---|---|---:|---:|---:|---:|---:|")
        for t in telas:
            for r in t["requisicoes"]:
                add(
                    f"| {t['tela']} | `{r['caminho']}` | {r['status']} | "
                    f"{r['p50_ms']:,.0f} ms | {r['p95_ms']:,.0f} ms | {r['max_ms']:,.0f} ms | "
                    f"{_mb(r['bytes'])} |"
                )
        add("")
        add("</details>")
        add("")
    return "\n".join(linhas)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
async def _executar(args: argparse.Namespace, banco: Path) -> dict[str, Any]:
    dados: dict[str, Any] = {
        "gerado_em": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "banco_origem": str(args.db),
        "repeticoes": args.repeticoes,
    }
    if "telas" in args.secao:
        usuario = admin_de_medicao(banco)
        if usuario is None:
            print("aviso: banco sem usuário — seção 'telas' pulada", file=sys.stderr)
        else:
            dados["telas"] = await medir_telas(banco, usuario, args.repeticoes)
    if "banco" in args.secao:
        dados["banco"] = medir_banco(banco)
    # Jobs por último: a retenção apaga linhas e o snapshot escreve arquivo.
    if "jobs" in args.secao:
        dados["jobs"] = await medir_jobs(banco)
    return dados


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--db", type=Path, default=REPO / "data" / "db" / "app.db")
    p.add_argument("--secao", action="append", choices=SECOES, default=None)
    p.add_argument("--repeticoes", type=int, default=5)
    p.add_argument("--out", type=Path, default=None, help="arquivo markdown do relatório")
    p.add_argument("--json", type=Path, default=None, help="arquivo com os números crus")
    p.add_argument("--manter", action="store_true", help="não apaga o diretório temporário")
    args = p.parse_args()
    args.secao = args.secao or list(SECOES)

    if not args.db.exists():
        print(f"banco não encontrado: {args.db}", file=sys.stderr)
        return 2

    trabalho = Path(tempfile.mkdtemp(prefix="perf-baseline-"))
    # APP_DATA_DIR antes de qualquer import do app: get_settings() é cacheado.
    os.environ["APP_DATA_DIR"] = str(trabalho)
    os.environ.setdefault("APP_LOG_LEVEL", "WARNING")
    try:
        banco = preparar_copia(args.db, trabalho)
        dados = asyncio.run(_executar(args, banco))
        texto = render_markdown(dados)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(texto, encoding="utf-8")
            print(f"relatório: {args.out}")
        else:
            print(texto)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(
                json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"números crus: {args.json}")
    finally:
        if args.manter:
            print(f"diretório de trabalho mantido: {trabalho}", file=sys.stderr)
        else:
            shutil.rmtree(trabalho, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
