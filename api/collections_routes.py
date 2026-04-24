import os
import json
from fastapi import APIRouter
from fastapi.responses import JSONResponse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECTION_DIR = os.path.join(BASE_DIR, "data", "collections")

router = APIRouter(prefix="/api/collections")


@router.get("/{type}")
def list_collections(type: str):
    path = os.path.join(COLLECTION_DIR, type)

    if not os.path.exists(path):
        return []

    return sorted(os.listdir(path), reverse=True)


@router.get("/{type}/{filename}")
def get_collection(type: str, filename: str):
    path = os.path.join(COLLECTION_DIR, type, filename)

    if not os.path.exists(path):
        return JSONResponse({"error": "Not found"}, status_code=404)

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)