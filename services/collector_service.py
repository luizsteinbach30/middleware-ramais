import os
import json
import httpx
from datetime import datetime
from core.config import load_config, load_devices, save_devices
from core.logger import write_log

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECTION_DIR = os.path.join(BASE_DIR, "data", "collections")


def ensure_dirs():
    os.makedirs(os.path.join(COLLECTION_DIR, "extensions"), exist_ok=True)


async def collect_extensions():
    config = load_config()
    ensure_dirs()

    host = config.get("uscall_host")
    token = config.get("uscall_token")

    if not host or not token:
        return

    try:
        url = f"https://{host}/api/extenstatus?token={token}&tipo=all"

        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            response = await client.get(url)

        if response.status_code != 200:
            return

        data = response.json()

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filepath = os.path.join(
            COLLECTION_DIR,
            "extensions",
            f"{timestamp}.json"
        )

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

        devices = load_devices()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for item in data:

            ramal = item.get("ramal")
            status = item.get("status")
            ip = item.get("ip")

            if not ramal:
                continue

            existing = next((d for d in devices if d["name"] == ramal), None)

            if existing:

                if ip and ip != existing.get("ip"):
                    existing["ip"] = ip
                    write_log("INFO", "extensions", f"IP atualizado: {ramal}")

                existing["logical_status"] = "available" if status == "disponivel" else "unavailable"

                if status == "disponivel":
                    existing["last_seen"] = now

                continue

            if ip:
                devices.append({
                    "name": ramal,
                    "ip": ip,
                    "type": "extension",
                    "logical_status": "available" if status == "disponivel" else "unavailable",
                    "network_status": "unknown",
                    "response_time": None,
                    "last_seen": now,
                    "last_ping": None
                })

        save_devices(devices)

    except Exception as e:
        write_log("ERROR", "extensions", str(e))