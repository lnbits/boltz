import asyncio
from loguru import logger
from typing import List

from fastapi import APIRouter

from lnbits.db import Database
from lnbits.helpers import template_renderer
from lnbits.tasks import create_permanent_unique_task, create_unique_task

db = Database("ext_boltz")


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


scheduled_tasks: List[asyncio.Task] = []


def boltz_stop():
    for task in scheduled_tasks:
        try:
            task.cancel()
        except Exception as ex:
            logger.warning(ex)


def boltz_start():
    pending_swaps = create_unique_task("ext_boltz_pending_swaps", check_for_pending_swaps())
    scheduled_tasks.append(pending_swaps)

    paid_invoices = create_unique_task("ext_boltz_paid_invoices", wait_for_paid_invoices())
    scheduled_tasks.append(paid_invoices)
