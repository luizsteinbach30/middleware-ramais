from fastapi import APIRouter
import os
import json

router = APIRouter(prefix="/api/webhook-logs")

LOG_PATH = "data/webhook_logs.json"


@router.get("/")
def get_logs():
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)