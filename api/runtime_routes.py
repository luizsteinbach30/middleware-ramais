import os
import json
from fastapi import APIRouter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_FILE = os.path.join(BASE_DIR, "data", "runtime_status.json")

router = APIRouter(prefix="/api/runtime")


@router.get("/")
def get_runtime_status():
    if not os.path.exists(STATUS_FILE):
        return {}
    with open(STATUS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)