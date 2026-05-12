"""Job registry — called by the FastAPI lifespan to wire up the scheduler."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.scheduler import add_cron_job, add_interval_job
from middleware_monitor.domain.config.repository import load_config

log = get_logger("jobs")


def register_all(scheduler: AsyncIOScheduler) -> None:
    """Register every recurring job. Imports happen lazily so the scheduler
    module stays decoupled from concrete jobs.
    """
    from middleware_monitor.jobs.collect_extensions import run_collect_extensions
    from middleware_monitor.jobs.monitor_devices import run_monitor_devices
    from middleware_monitor.jobs.retention import run_retention
    from middleware_monitor.updater.service import run_update_check

    with session_factory() as db:
        cfg = load_config(db)

    add_interval_job(
        run_collect_extensions,
        job_id="collect_extensions",
        seconds=cfg.extensions_interval_seconds,
    )
    add_interval_job(
        run_monitor_devices,
        job_id="monitor_devices",
        seconds=cfg.devices_interval_seconds,
    )
    # Retention runs once a day at 00:30 UTC.
    add_cron_job(
        run_retention,
        job_id="retention_daily",
        hour=0,
        minute=30,
    )
    # Update check runs once a day at 00:00 UTC, as required by the operator.
    add_cron_job(
        run_update_check,
        job_id="update_check",
        hour=0,
        minute=0,
    )

    log.info(
        "jobs_registered",
        collect_seconds=cfg.extensions_interval_seconds,
        monitor_seconds=cfg.devices_interval_seconds,
    )
