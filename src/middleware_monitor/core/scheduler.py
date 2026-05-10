"""Single AsyncIO scheduler instance for the whole process.

Jobs are registered through ``register_all`` during the FastAPI lifespan. Each
job module exposes a ``register(scheduler)`` function reading the interval from
``app_config``. Reconfiguration when ``app_config`` changes is the
responsibility of the config repository (it calls ``reschedule``).
"""

from __future__ import annotations

from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from middleware_monitor.core.logging import get_logger

log = get_logger("scheduler")

_scheduler: AsyncIOScheduler | None = None

_DEFAULTS = {
    "max_instances": 1,
    "coalesce": True,
    "misfire_grace_time": 60,
}


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC", job_defaults=_DEFAULTS)
    return _scheduler


def start() -> None:
    sched = get_scheduler()
    if not sched.running:
        sched.start()
        log.info("scheduler_started", jobs=[j.id for j in sched.get_jobs()])


def shutdown(wait: bool = False) -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=wait)
        log.info("scheduler_stopped")
    _scheduler = None


def add_interval_job(func: Any, *, job_id: str, seconds: int, **kwargs: Any) -> None:
    sched = get_scheduler()
    sched.add_job(
        func,
        "interval",
        seconds=seconds,
        id=job_id,
        replace_existing=True,
        **kwargs,
    )


def reschedule(job_id: str, seconds: int) -> None:
    sched = get_scheduler()
    if sched.get_job(job_id):
        sched.reschedule_job(job_id, trigger="interval", seconds=seconds)
        log.info("job_rescheduled", job_id=job_id, seconds=seconds)
