from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from core.config import load_config

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    config = load_config()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "config": config
    })