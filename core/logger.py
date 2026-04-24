import json
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(BASE_DIR, "data", "logs.json")

def write_log(level, module, message):
    log_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "module": module,
        "message": message
    }

    if not os.path.exists(LOG_FILE):
        logs = []
    else:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)

    logs.insert(0, log_entry)

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs[:500], f, indent=4)