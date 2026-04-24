import json
import os
from fastapi import APIRouter

router = APIRouter(prefix="/api/logs")

LOG_FILE = os.path.join("data", "logs.json")

@router.get("/")
def get_logs():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)