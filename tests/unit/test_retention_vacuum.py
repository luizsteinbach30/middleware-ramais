"""Compactação do banco depois da poda.

O SQLite não devolve ao sistema de arquivos a página que a poda esvaziou — ela
vira freelist, e o arquivo só cresce. Medido no banco do cliente em 2026-08-24:
44% do arquivo era espaço livre (24,4 de 55,4 MB). Estes testes prendem as duas
metades da decisão: compactar quando há espaço de verdade a recuperar, e **não**
reescrever o arquivo inteiro quando não há.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

from middleware_monitor.core.db import get_engine
from middleware_monitor.jobs.retention import _compactar

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as DBSession


def _encher_e_esvaziar(db: DBSession, megabytes: int) -> None:
    """Cria espaço livre de verdade: grava `megabytes` e apaga tudo."""
    db.execute(text("CREATE TABLE lixo (id INTEGER PRIMARY KEY, bloco BLOB)"))
    bloco = b"x" * 4096
    for _ in range(megabytes):
        db.execute(
            text("INSERT INTO lixo (bloco) VALUES (:b)"),
            [{"b": bloco} for _ in range(256)],  # 256 de 4 KB = 1 MB por lote
        )
    db.commit()
    db.execute(text("DELETE FROM lixo"))
    db.commit()


def test_banco_sem_espaco_livre_nao_e_reescrito(db: DBSession) -> None:
    """Um banco recém-criado não tem o que recuperar — VACUUM aqui seria só
    custo."""
    db.commit()

    resultado = _compactar()

    assert resultado["compactou"] is False
    assert resultado["motivo"] == "abaixo_do_limiar"


def test_espaco_livre_acima_do_limiar_e_devolvido(db: DBSession) -> None:
    _encher_e_esvaziar(db, megabytes=12)
    engine = get_engine()
    with engine.connect() as conn:
        page_size = int(conn.exec_driver_sql("PRAGMA page_size").scalar() or 0)
        antes = int(conn.exec_driver_sql("PRAGMA page_count").scalar() or 0)
        livres = int(conn.exec_driver_sql("PRAGMA freelist_count").scalar() or 0)
    assert livres * page_size > 8 * 1024 * 1024, "cenário não gerou freelist suficiente"

    resultado = _compactar()

    assert resultado["compactou"] is True
    assert resultado["liberado_mb"] > 8
    with engine.connect() as conn:
        depois = int(conn.exec_driver_sql("PRAGMA page_count").scalar() or 0)
    assert depois < antes
