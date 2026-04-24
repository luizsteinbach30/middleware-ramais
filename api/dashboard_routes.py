from fastapi import APIRouter
import os
import json
from datetime import datetime

router = APIRouter(prefix="/api/dashboard")

DEVICES_PATH = "data/devices.json"
WEBHOOK_LOG_PATH = "data/webhook_logs.json"
COLLECTIONS_PATH = "data/collections/extensions"


def load_json(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return []


@router.get("/")
def get_dashboard():

    devices = load_json(DEVICES_PATH)
    logs = load_json(WEBHOOK_LOG_PATH)

    total_devices = len(devices)
    network_online = sum(1 for d in devices if d.get("status") == "online")
    network_offline = total_devices - network_online

    logical_available = sum(1 for d in devices if d.get("logical_status") == "disponivel")
    logical_unavailable = total_devices - logical_available

    latencies = [d.get("latency") for d in devices if d.get("latency")]

    avg_latency = int(sum(latencies)/len(latencies)) if latencies else 0
    max_latency = max(latencies) if latencies else 0

    # Última coleta
    last_collection = None
    if os.path.exists(COLLECTIONS_PATH):
        files = os.listdir(COLLECTIONS_PATH)
        if files:
            last_collection = max(files)

    last_webhook_status = None
    if logs:
        last_webhook_status = "success" if logs[-1].get("success") else "failure"

    return {
        "monitor_running": True,
        "last_collection": last_collection,
        "total_devices": total_devices,
        "network_online": network_online,
        "network_offline": network_offline,
        "logical_available": logical_available,
        "logical_unavailable": logical_unavailable,
        "avg_latency": avg_latency,
        "max_latency": max_latency,
        "webhook_logs_count": len(logs),
        "last_webhook_status": last_webhook_status
    }