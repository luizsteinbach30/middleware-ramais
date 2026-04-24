import os
import json
from datetime import datetime, timedelta

DATA_DIR = "data"
LOG_FILE = os.path.join(DATA_DIR, "webhook_logs.json")


def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)


def load_webhook_logs():
    ensure_data_dir()

    if not os.path.exists(LOG_FILE):
        return []

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def save_webhook_log(entry):
    ensure_data_dir()

    logs = load_webhook_logs()
    logs.append(entry)

    logs = apply_retention(logs)

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4)


def apply_retention(logs):
    """
    Remove logs mais antigos que o periodo configurado
    """
    try:
        from core.config import load_config
        config = load_config()
        retention_days = config.get("webhook_log_retention_days", 30)

        cutoff = datetime.now() - timedelta(days=retention_days)

        filtered = []

        for log in logs:
            try:
                log_time = datetime.strptime(
                    log.get("timestamp"),
                    "%Y-%m-%d %H:%M:%S"
                )

                if log_time >= cutoff:
                    filtered.append(log)
            except:
                continue

        return filtered

    except:
        return logs