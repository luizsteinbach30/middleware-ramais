from fastapi import APIRouter

from core.config import load_devices
from services.monitor_service import collect_extensions

router = APIRouter()


@router.get("/api/devices")
def list_devices():

    return load_devices()


@router.post("/api/devices/force-monitor")
def force_monitor():

    collect_extensions()

    return {"status": "monitor executed"}