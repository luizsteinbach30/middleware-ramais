"""Job registry — called by the FastAPI lifespan to wire up the scheduler."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.scheduler import (
    add_cron_job,
    add_interval_job,
    remove_job,
    reschedule_cron,
)
from middleware_monitor.domain.config.repository import load_config
from middleware_monitor.domain.config.update_settings import (
    UpdateSettings,
    load_update_settings,
)

log = get_logger("jobs")

UPDATE_JOB_ID = "update_check"


def register_all(scheduler: AsyncIOScheduler) -> None:
    """Register every recurring job. Imports happen lazily so the scheduler
    module stays decoupled from concrete jobs.
    """
    from middleware_monitor.jobs.collect_extensions import run_collect_extensions
    from middleware_monitor.jobs.monitor_devices import run_monitor_devices
    from middleware_monitor.jobs.retention import run_retention

    with session_factory() as db:
        cfg = load_config(db)
        upd = load_update_settings(db)

    # A single user-facing knob drives how often we collect from USCall and
    # ping/dispatch webhooks. Stored as minutes, applied here in seconds.
    interval_seconds = max(60, cfg.webhook_interval_minutes * 60)

    add_interval_job(
        run_collect_extensions,
        job_id="collect_extensions",
        seconds=interval_seconds,
    )
    add_interval_job(
        run_monitor_devices,
        job_id="monitor_devices",
        seconds=interval_seconds,
    )
    # Retention runs once a day at 00:30 UTC.
    add_cron_job(
        run_retention,
        job_id="retention_daily",
        hour=0,
        minute=30,
    )
    # Verificação de update: horário/dias configuráveis pela tela (v2.7.0).
    # O job SÓ verifica e avisa — instalar continua sendo ação manual.
    apply_update_schedule(upd)

    log.info(
        "jobs_registered",
        interval_minutes=cfg.webhook_interval_minutes,
        update_auto_check=upd.auto_check,
        update_check_at=f"{upd.check_hour:02d}:{upd.check_minute:02d}",
        update_check_days=upd.day_of_week,
    )


def apply_update_schedule(upd: UpdateSettings) -> None:
    """(Re)agenda a verificação de update conforme a config, ou a remove.

    Chamado no boot e sempre que a tela salva — assim mudar horário/dias vale
    na hora, sem reiniciar o serviço.
    """
    from middleware_monitor.updater.service import run_update_check

    if not upd.auto_check:
        remove_job(UPDATE_JOB_ID)
        log.info("update_check_disabled")
        return
    tz = _local_timezone()
    if not reschedule_cron(
        UPDATE_JOB_ID,
        hour=upd.check_hour,
        minute=upd.check_minute,
        day_of_week=upd.day_of_week,
        timezone=tz,
    ):
        add_cron_job(
            run_update_check,
            job_id=UPDATE_JOB_ID,
            hour=upd.check_hour,
            minute=upd.check_minute,
            day_of_week=upd.day_of_week,
            timezone=tz,
        )
        log.info(
            "update_check_scheduled",
            at=f"{upd.check_hour:02d}:{upd.check_minute:02d}",
            days=upd.day_of_week,
        )


def _local_timezone() -> Any:
    """Fuso local do servidor — o operador escolhe o horário no relógio dele.

    O scheduler roda em UTC; sem isto, "03:00" na tela viraria 00:00 local no
    Brasil. Se o SO não expõe o fuso, cai em UTC (comportamento antigo).
    """
    tz = datetime.now().astimezone().tzinfo
    return tz or UTC
