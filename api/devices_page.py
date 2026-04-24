from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/devices", response_class=HTMLResponse)
async def devices_page(request: Request):
    return templates.TemplateResponse("devices.html", {
        "request": request
    })