"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

# Configure environment BEFORE importing the app.
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-must-be-long-enough")


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_LOG_LEVEL", "WARNING")

    from middleware_monitor.settings import get_settings

    get_settings.cache_clear()
    from middleware_monitor.core.db import reset_engine_for_tests

    reset_engine_for_tests()
    yield tmp_path
    reset_engine_for_tests()
    get_settings.cache_clear()


@pytest.fixture
def db(isolated_data_dir: Path) -> Iterator[Session]:
    from middleware_monitor.core.db import Base, init_engine, session_factory

    engine = init_engine()
    Base.metadata.create_all(engine)
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    from middleware_monitor.app import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
