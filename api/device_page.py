from fastapi import APIRouter
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from core.config import load_config

router = APIRouter()

templates = Jinja2Templates(directory="templates")


@router.get("/devices")
def devices_page(request: Request):

    config = load_config()

    return templates.TemplateResponse(
        "devices.html",
        {
            "request": request,
            "config": config
        }
    )