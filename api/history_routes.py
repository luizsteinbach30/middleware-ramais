from fastapi import APIRouter
import os
import json

router = APIRouter()

COLLECTIONS_DIR = "data/collections/extensions"


@router.get("/api/collections/extensions")
def list_extension_collections():

    if not os.path.exists(COLLECTIONS_DIR):
        return []

    files = sorted(
        os.listdir(COLLECTIONS_DIR),
        reverse=True
    )

    return files


@router.get("/api/collections/extensions/{file}")
def get_extension_collection(file: str):

    path = os.path.join(COLLECTIONS_DIR, file)

    if not os.path.exists(path):
        return {"error": "not found"}

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)