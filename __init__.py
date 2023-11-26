import asyncio
from typing import List

from fastapi import APIRouter

from lnbits.db import Database
from lnbits.helpers import template_renderer
from lnbits.tasks import create_permanent_task, create_task

db = Database("ext_boltz")

scheduled_tasks: List[asyncio.Task] = []

boltz_ext: APIRouter = APIRouter(prefix="/boltz", tags=["boltz"])


def boltz_renderer():
    return template_renderer(["boltz/templates"])


boltz_static_files = [
    {
        "path": "/boltz/static",
        "name": "boltz_static",
    }
]

from .tasks import check_for_pending_swaps, wait_for_paid_invoices
from .views import *  # noqa: F401,F403
from .views_api import *  # noqa: F401,F403


def boltz_start():
    scheduled_tasks.append(create_task(check_for_pending_swaps()))
    scheduled_tasks.append(create_permanent_task(wait_for_paid_invoices))
