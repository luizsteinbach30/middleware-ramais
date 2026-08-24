"""Snapshot do banco: criacao, validacao, poda e a troca no boot.

O caso que justifica o desenho todo esta em
``test_restauracao_so_acontece_no_proximo_boot``: enquanto o app roda, o
arquivo do banco esta aberto, entao restaurar agenda a troca em vez de fazer na
hora — e o banco substituido nunca e apagado.
"""

from __future__ import annotations

import gzip
import time
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from middleware_monitor.domain.backup import snapshot as snap


def _com_alembic(db: Session) -> None:
    """O conftest cria as tabelas por ``metadata``, sem a ``alembic_version``
    que existe em qualquer banco real — e que a validacao exige."""
    revisao = sorted(snap.known_revisions())[0]
    db.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
    db.execute(text("DELETE FROM alembic_version"))
    db.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": revisao})
    db.commit()


def test_snapshot_gera_arquivo_valido_e_inspecionavel(db: Session) -> None:
    _com_alembic(db)
    caminho = snap.create_snapshot(label="manual")

    assert caminho.name.endswith(".db.gz")
    assert caminho.parent == snap.backups_dir()
    resumo = snap.inspect_file(caminho)
    assert resumo["migration"] in snap.known_revisions()
    assert "devices" in resumo["counts"]


def test_inspect_recusa_arquivo_que_nao_e_banco(db: Session, tmp_path: Path) -> None:
    intruso = snap.backups_dir() / "lixo.db.gz"
    with gzip.open(intruso, "wb") as fh:
        fh.write(b"isto nao e um banco")
    with pytest.raises(snap.SnapshotError, match="nao e um banco SQLite"):
        snap.inspect_file(intruso)


def test_inspect_recusa_banco_de_outro_sistema(db: Session) -> None:
    """SQLite valido, mas sem as tabelas do middleware."""
    import sqlite3

    estranho = snap.backups_dir() / "estranho.db"
    conn = sqlite3.connect(str(estranho))
    conn.execute("CREATE TABLE outra_coisa (id INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(snap.SnapshotError, match="outro sistema"):
        snap.inspect_file(estranho)


def test_inspect_recusa_migration_desconhecida(db: Session) -> None:
    """Backup de uma versão mais nova traria schema que este código não lê."""
    db.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
    db.execute(text("INSERT INTO alembic_version (version_num) VALUES ('9999_do_futuro')"))
    db.commit()
    caminho = snap.create_snapshot()
    with pytest.raises(snap.SnapshotError, match="versao mais nova"):
        snap.inspect_file(caminho)


def test_poda_por_quantidade_mantem_os_mais_recentes(db: Session) -> None:
    _com_alembic(db)
    nomes = []
    for _ in range(4):
        nomes.append(snap.create_snapshot(label="auto").name)
        time.sleep(1.05)  # o nome e a ordenacao usam o segundo

    removidos = snap.prune(keep=2, max_bytes=0)

    restantes = [b.name for b in snap.list_backups()]
    assert len(restantes) == 2
    assert set(restantes) == set(nomes[-2:])
    assert set(removidos) == set(nomes[:2])


def test_poda_por_espaco_nunca_deixa_zero_backups(db: Session) -> None:
    _com_alembic(db)
    snap.create_snapshot(label="auto")
    time.sleep(1.05)
    snap.create_snapshot(label="auto")

    snap.prune(keep=10, max_bytes=1)  # teto absurdo de propósito

    assert len(snap.list_backups()) == 1


def test_resolve_bloqueia_caminho_para_fora_da_pasta(db: Session) -> None:
    for nome in ("../app.db", "..", "", "sub/dir.db"):
        with pytest.raises(snap.SnapshotError):
            snap.resolve(nome)


def test_restauracao_so_acontece_no_proximo_boot(db: Session) -> None:
    _com_alembic(db)
    db.execute(text("INSERT INTO app_config (key, value, is_secret, updated_at) "
                    "VALUES ('marcador', 'do-backup', 0, '2026-01-01 00:00:00')"))
    db.commit()
    caminho = snap.create_snapshot()

    # o estado muda depois do snapshot
    db.execute(text("UPDATE app_config SET value = 'depois-do-backup' WHERE key = 'marcador'"))
    db.commit()

    meta = snap.schedule_restore(caminho)
    assert meta["source"] == caminho.name
    assert snap.pending_restore() is not None
    # nada foi trocado ainda: o app segue lendo o banco atual
    atual = db.execute(text("SELECT value FROM app_config WHERE key='marcador'")).scalar()
    assert atual == "depois-do-backup"

    db.close()
    from middleware_monitor.core.db import reset_engine_for_tests

    reset_engine_for_tests()
    resultado = snap.apply_pending_restore()

    assert resultado is not None and resultado["status"] == "ok"
    assert snap.pending_restore() is None
    # o banco anterior virou pre-restore-*, não foi apagado
    anteriores = [b for b in snap.list_backups() if b.kind == "pre-restore"]
    assert len(anteriores) == 1

    import sqlite3

    conn = sqlite3.connect(str(snap.db_path()))
    try:
        valor = conn.execute("SELECT value FROM app_config WHERE key='marcador'").fetchone()[0]
    finally:
        conn.close()
    assert valor == "do-backup"


def test_cancelar_restauracao_pendente(db: Session) -> None:
    _com_alembic(db)
    snap.schedule_restore(snap.create_snapshot())
    assert snap.cancel_pending_restore() is True
    assert snap.pending_restore() is None
    assert snap.apply_pending_restore() is None
