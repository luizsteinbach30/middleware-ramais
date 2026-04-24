from apscheduler.schedulers.asyncio import AsyncIOScheduler
from services.collector_service import collect_extensions
from services.monitor_service import monitor_devices
from core.config import load_config
from core.logger import write_log

scheduler = AsyncIOScheduler()


def start_scheduler():
    config = load_config()

    scheduler.add_job(
        collect_extensions,
        "interval",
        seconds=config.get("extensions_interval", 60),
        id="extensions_job",
        replace_existing=True
    )

    scheduler.add_job(
        monitor_devices,
        "interval",
        seconds=config.get("devices_ping_interval", 30),
        id="monitor_job",
        replace_existing=True
    )

    scheduler.start()
    write_log("INFO", "scheduler", "Scheduler iniciado")