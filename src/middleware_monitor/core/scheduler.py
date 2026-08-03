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


def add_cron_job(
    func: Any,
    *,
    job_id: str,
    hour: int = 0,
    minute: int = 0,
    day_of_week: str | None = None,
    timezone: Any = None,
    **kwargs: Any,
) -> None:
    """Cron-style job. Defaults to 00:00 every day (UTC).

    ``timezone`` permite agendar no fuso local do servidor (o scheduler roda em
    UTC): o operador escolhe "03:00" e é 03:00 no relógio dele.
    """
    sched = get_scheduler()
    extra: dict[str, Any] = {}
    if day_of_week:
        extra["day_of_week"] = day_of_week
    if timezone is not None:
        extra["timezone"] = timezone
    sched.add_job(
        func,
        "cron",
        hour=hour,
        minute=minute,
        id=job_id,
        replace_existing=True,
        **extra,
        **kwargs,
    )


def remove_job(job_id: str) -> None:
    """Remove o job se existir (usado ao desligar a verificação automática)."""
    sched = get_scheduler()
    if sched.get_job(job_id):
        sched.remove_job(job_id)
        log.info("job_removed", job_id=job_id)


def reschedule(job_id: str, seconds: int) -> None:
    sched = get_scheduler()
    if sched.get_job(job_id):
        sched.reschedule_job(job_id, trigger="interval", seconds=seconds)
        log.info("job_rescheduled", job_id=job_id, seconds=seconds)


def reschedule_cron(
    job_id: str,
    *,
    hour: int,
    minute: int,
    day_of_week: str | None = None,
    timezone: Any = None,
) -> bool:
    """Reagenda um job cron sem reiniciar o processo.

    ``reschedule`` só sabe trigger ``interval``; a config de auto-update precisa
    trocar hora/dias em runtime. Devolve ``False`` se o job não está registrado
    (ex.: verificação automática desligada) — o caller decide recriá-lo.
    """
    sched = get_scheduler()
    if not sched.get_job(job_id):
        return False
    kwargs: dict[str, Any] = {"hour": hour, "minute": minute}
    if day_of_week:
        kwargs["day_of_week"] = day_of_week
    if timezone is not None:
        kwargs["timezone"] = timezone
    sched.reschedule_job(job_id, trigger="cron", **kwargs)
    log.info(
        "job_rescheduled_cron", job_id=job_id, hour=hour, minute=minute,
        day_of_week=day_of_week or "*",
    )
    return True
