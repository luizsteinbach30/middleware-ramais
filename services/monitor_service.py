import os
import json
import time
import requests
from datetime import datetime, timedelta
import subprocess
import platform
import threading

CONFIG_PATH = "data/config.json"
DEVICES_PATH = "data/devices.json"
COLLECTIONS_PATH = "data/collections/extensions"
WEBHOOK_LOG_PATH = "data/webhook_logs.json"


# ================= UTIL =================

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_devices():
    if not os.path.exists(DEVICES_PATH):
        return []
    with open(DEVICES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_devices(devices):
    os.makedirs("data", exist_ok=True)
    with open(DEVICES_PATH, "w", encoding="utf-8") as f:
        json.dump(devices, f, indent=4)


# ================= WEBHOOK LOGGER =================

def log_webhook(entry: dict):
    os.makedirs("data", exist_ok=True)

    logs = []

    if os.path.exists(WEBHOOK_LOG_PATH):
        try:
            with open(WEBHOOK_LOG_PATH, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    logs = json.loads(content)
        except:
            logs = []

    logs.append(entry)

    config = load_config()
    retention_days = config.get("webhook_log_retention_days", 30)
    cutoff = datetime.now() - timedelta(days=retention_days)

    filtered = [
        l for l in logs
        if datetime.strptime(l["timestamp"], "%Y-%m-%d %H:%M:%S") > cutoff
    ]

    with open(WEBHOOK_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(filtered, f, indent=4)


# ================= WEBHOOK SEND =================

def send_webhook(event_type: str, payload):

    print(f">>> TENTANDO ENVIAR WEBHOOK: {event_type}")

    config = load_config()
    webhook_cfg = config.get("webhooks", {}).get(event_type, {})

    if not webhook_cfg.get("enabled") or not webhook_cfg.get("url"):
        print("Webhook desabilitado ou sem URL.")
        return

    url = webhook_cfg["url"]
    token = webhook_cfg.get("token")
    client_code = config.get("client_code")

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # 🔥 GARANTE QUE O PAYLOAD SEMPRE VIRE UM OBJETO PADRÃO
    wrapped_payload = {
        "client_code": client_code,
        "event_type": event_type,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": payload,
        "test": False
    }

    start = time.time()
    success = False
    http_status = None
    response_body = None
    error = None

    try:
        response = requests.post(
            url,
            json=wrapped_payload,
            headers=headers,
            timeout=10
        )

        http_status = response.status_code
        response_body = response.text
        success = 200 <= response.status_code < 300

        print(f"Webhook enviado. Status: {http_status}")

    except Exception as e:
        error = str(e)
        print(f"Erro ao enviar webhook: {error}")

    duration_ms = int((time.time() - start) * 1000)

    log_webhook({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type": event_type,
        "url": url,
        "payload": wrapped_payload,
        "http_status": http_status,
        "response_body": response_body,
        "success": success,
        "error": error,
        "duration_ms": duration_ms
    })


# ================= COLETA =================

def collect_extensions():
    config = load_config()
    host = config.get("uscall_host")
    token = config.get("uscall_token")

    if not host or not token:
        return

    url = f"https://{host}/api/extenstatus?token={token}&tipo=all"

    try:
        response = requests.get(url, verify=False, timeout=10)
        data = json.loads(response.content.decode("utf-8-sig"))

        os.makedirs(COLLECTIONS_PATH, exist_ok=True)
        filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".json"

        with open(os.path.join(COLLECTIONS_PATH, filename), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

        process_devices_from_collection(data)

        send_webhook("extensions", data)

    except Exception as e:
        print("USCALL COLLECTION ERROR:", e)


# ================= DEVICES =================

def process_devices_from_collection(data):
    devices = load_devices()

    if not isinstance(data, list):
        print("ERRO: data não é lista")
        return

    for ext in data:
        name = str(ext.get("ramal"))
        ip = ext.get("ip")
        status = ext.get("status")

        existing = next((d for d in devices if d["name"] == name), None)

        if status == "disponivel" and ip:
            if existing:
                existing["ip"] = ip
                existing["logical_status"] = status
            else:
                devices.append({
                    "name": name,
                    "ip": ip,
                    "logical_status": status,
                    "status": "offline",
                    "latency": None,
                    "last_ping": None,
                    "mac": None
                })
        elif existing:
            existing["logical_status"] = status

    save_devices(devices)


def ping(ip):
    param = "-n" if platform.system().lower() == "windows" else "-c"
    try:
        output = subprocess.check_output(["ping", param, "1", ip], universal_newlines=True)
        if "TTL=" in output.upper():
            return True
    except:
        pass
    return False


def get_mac(ip):
    try:
        output = subprocess.check_output("arp -a", universal_newlines=True)
        for line in output.splitlines():
            if ip in line:
                parts = line.split()
                if len(parts) >= 2:
                    return parts[1]
    except:
        pass
    return None


def monitor_devices():
    devices = load_devices()

    for d in devices:
        ip = d.get("ip")
        if not ip:
            continue

        start = time.time()
        online = ping(ip)
        duration = int((time.time() - start) * 1000)

        d["status"] = "online" if online else "offline"
        d["latency"] = duration if online else None
        d["last_ping"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if online:
            mac = get_mac(ip)
            if mac:
                d["mac"] = mac

    save_devices(devices)

    send_webhook("devices", devices)


# ================= LOOP =================

def monitor_loop():
    print("Monitor loop started")

    while True:
        config = load_config()
        ext_interval = config.get("extensions_interval", 20)
        dev_interval = config.get("devices_interval", 60)

        collect_extensions()
        monitor_devices()

        time.sleep(min(ext_interval, dev_interval))


def start_monitor():
    print("Starting monitor")
    thread = threading.Thread(target=monitor_loop, daemon=True)
    thread.start()