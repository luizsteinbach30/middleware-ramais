from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/collections", response_class=HTMLResponse)
async def collections_page(request: Request):
    return templates.TemplateResponse("collections.html", {
        "request": request
    })