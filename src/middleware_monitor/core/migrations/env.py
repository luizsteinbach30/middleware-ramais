"""Alembic environment.

We import the application's ``Base`` and models so ``--autogenerate`` works,
and override ``sqlalchemy.url`` from settings/env at runtime.
"""

from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from middleware_monitor.core import models  # noqa: F401  ensure models are imported
from middleware_monitor.core.db import Base
from middleware_monitor.settings import get_settings

config = context.config

# fileConfig REMOVE os handlers já instalados (disable_existing_loggers) — no
# modo desktop isso matava o app.log e a aba de Log no meio do boot, deixando
# qualquer falha posterior invisível. Só configura logging quando rodando via
# CLI do alembic (nenhum handler instalado ainda).
if config.config_file_name is not None and not logging.getLogger().hasHandlers():
    fileConfig(config.config_file_name, disable_existing_loggers=False)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.effective_db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite-friendly DDL
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
