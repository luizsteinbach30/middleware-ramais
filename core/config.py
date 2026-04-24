import os
import json

DATA_DIR = "data"

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
DEVICES_FILE = os.path.join(DATA_DIR, "devices.json")
PING_HISTORY_FILE = os.path.join(DATA_DIR, "ping_history.json")


def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)


# =========================
# CONFIG
# =========================

def default_config():
    return {
        "client_code": "CLI001",

        "devices_ping_interval": 30,
        "offline_alert_minutes": 5,

        "webhook_enabled": False,
        "webhook_url": "",
        "webhook_send_token": True,
        "webhook_token": "",
        "webhook_timeout_seconds": 10,
        "webhook_log_retention_days": 30
    }


def load_config():
    ensure_data_dir()

    if not os.path.exists(CONFIG_FILE):
        config = default_config()
        save_config(config)
        return config

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        config = default_config()
        save_config(config)
        return config


def save_config(config):
    ensure_data_dir()

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)


# =========================
# DEVICES
# =========================

def load_devices():
    ensure_data_dir()

    if not os.path.exists(DEVICES_FILE):
        return []

    try:
        with open(DEVICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def save_devices(devices):
    ensure_data_dir()

    with open(DEVICES_FILE, "w", encoding="utf-8") as f:
        json.dump(devices, f, indent=4)


# =========================
# PING HISTORY
# =========================

def load_ping_history():
    ensure_data_dir()

    if not os.path.exists(PING_HISTORY_FILE):
        return []

    try:
        with open(PING_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def save_ping_history(history):
    ensure_data_dir()

    with open(PING_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=4)