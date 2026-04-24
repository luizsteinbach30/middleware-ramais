from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os
import json

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# 🔥 Página do gráfico usando device_chart.html
@router.get("/devices/{name}", response_class=HTMLResponse)
def device_chart_page(request: Request, name: str):
    return templates.TemplateResponse("device_chart.html", {
        "request": request,
        "device": name
    })


# 🔥 API usada pelo gráfico
@router.get("/api/history/{name}")
def device_history(name: str):

    path = f"data/history/{name}.json"

    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []