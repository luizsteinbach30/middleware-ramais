"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
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


@pytest.fixture(autouse=True)
def reset_mqtt_ingestor() -> Iterator[None]:
    """O coletor MQTT é instância única no processo (``workers=1``, ADR-0001).

    Sem zerar entre os testes, o estado ao vivo dos ramais de um teste
    reapareceria no seguinte — e o painel passaria a ser verificado contra
    dado de outro cenário.
    """
    from middleware_monitor.domain.mqtt import links as mqtt_links
    from middleware_monitor.domain.mqtt import service as mqtt_service
    from middleware_monitor.domain.mqtt import storage as mqtt_storage

    mqtt_service._ingestor = None
    # O índice ramal → device/ambiente também é de processo, com TTL de 30 s:
    # sem zerar, o teste seguinte veria o vínculo do anterior.
    mqtt_links.invalidate()
    # Idem para a ocupação do ledger (TTL de 60 s): sem zerar, o teste que grava
    # mensagens leria o total do teste anterior — normalmente zero.
    mqtt_storage.invalidate()
    yield
    mqtt_service._ingestor = None
    mqtt_links.invalidate()
    mqtt_storage.invalidate()


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
