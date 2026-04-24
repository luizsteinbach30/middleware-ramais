from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from core.config import load_config

from api.devices_routes import router as devices_router
from api.history_routes import router as history_router
from api.history_page import router as history_page_router
from api.config_routes import router as config_router
from api.config_page import router as config_page_router
from api.device_page import router as device_page_router
from api.webhook_logs_routes import router as webhook_logs_router
from api.webhook_logs_page import router as webhook_logs_page_router
from api.dashboard_routes import router as dashboard_router
from api.webhook_routes import router as webhook_router
from api.device_routes import router as device_router

from services.monitor_service import start_monitor


app = FastAPI()

templates = Jinja2Templates(directory="templates")

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
def startup():

    print("Starting monitor")

    start_monitor()


@app.get("/")
def dashboard(request: Request):

    config = load_config()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "config": config
        }
    )


app.include_router(devices_router)
app.include_router(history_router)
app.include_router(history_page_router)
app.include_router(config_router)
app.include_router(config_page_router)
app.include_router(device_page_router)
app.include_router(webhook_logs_router)
app.include_router(webhook_logs_page_router)
app.include_router(dashboard_router)
app.include_router(webhook_router)
app.include_router(device_router)