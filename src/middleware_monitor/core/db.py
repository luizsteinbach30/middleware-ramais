"""SQLAlchemy 2.0 engine + session helpers.

We pin ``journal_mode=WAL`` and ``foreign_keys=ON`` for every SQLite connection
and use a synchronous ``Session`` because the DB is local — async would buy
nothing here and would complicate transaction handling in jobs.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from middleware_monitor.settings import get_settings


class Base(DeclarativeBase):
    """Common declarative base."""


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _enable_sqlite_pragmas(dbapi_conn: Any, _conn_record: Any) -> None:
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def _apply_pending_restore() -> None:
    """Troca o banco por um snapshot restaurado, antes de qualquer conexão.

    Este é o único instante seguro para a troca: o arquivo ainda não está
    aberto por ninguém. Import tardio porque ``domain.backup`` não pode ser
    carregado no topo (``core.models`` importa ``Base`` daqui). Falha aqui não
    derruba o boot — o app sobe com o banco atual e a tela reporta.
    """
    try:
        from middleware_monitor.domain.backup.snapshot import apply_pending_restore

        apply_pending_restore()
    except Exception:  # pragma: no cover - defensivo no caminho de boot
        logging.getLogger("backup").exception("falha ao aplicar restauracao pendente")


def init_engine(url: str | None = None) -> Engine:
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine
    if url is None:
        # Só no caminho normal: com URL explícita (testes, alembic apontado a
        # outro arquivo) não faz sentido mexer no banco da instalação.
        _apply_pending_restore()
    db_url = url or get_settings().effective_db_url
    is_sqlite = db_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    _engine = create_engine(db_url, connect_args=connect_args, future=True)
    if is_sqlite:
        event.listen(_engine, "connect", _enable_sqlite_pragmas)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        return init_engine()
    return _engine


def session_factory() -> Session:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


def reset_engine_for_tests() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
