from fastapi import APIRouter
from core.config import load_config, save_config

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/")
def get_config():
    return load_config()


@router.post("/")
def update_config(new_config: dict):
    config = load_config()
    config.update(new_config)
    save_config(config)
    return {"status": "config_saved"}