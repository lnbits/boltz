from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from lnbits.core.models import User
from lnbits.decorators import check_user_exists
from lnbits.helpers import template_renderer

boltz_generic_router = APIRouter()
templates = Jinja2Templates(directory="templates")


def boltz_renderer():
    return template_renderer(["boltz/templates"])


boltz_generic_router.get("/", response_class=HTMLResponse)


async def index(request: Request, user: User = Depends(check_user_exists)):
    root_url = urlparse(str(request.url)).netloc
    return boltz_renderer().TemplateResponse(
        "boltz/index.html",
        {"request": request, "user": user.dict(), "root_url": root_url},
    )
