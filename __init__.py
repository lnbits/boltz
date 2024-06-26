import asyncio
from typing import List

from fastapi import APIRouter
from loguru import logger

from .crud import db
from .tasks import check_for_pending_swaps, wait_for_paid_invoices
from .views import boltz_generic_router
from .views_api import boltz_api_router

boltz_ext = APIRouter(prefix="/boltz", tags=["boltz"])
boltz_ext.include_router(boltz_generic_router)
boltz_ext.include_router(boltz_api_router)


boltz_static_files = [
    {
        "path": "/boltz/static",
        "name": "boltz_static",
    }
]

scheduled_tasks: List[asyncio.Task] = []


def boltz_stop():
    for task in scheduled_tasks:
        try:
            task.cancel()
        except Exception as ex:
            logger.warning(ex)


def boltz_start():
    from lnbits.tasks import create_permanent_unique_task, create_unique_task

    pending_swaps = create_unique_task(
        "ext_boltz_pending_swaps", check_for_pending_swaps()
    )
    scheduled_tasks.append(pending_swaps)

    paid_invoices = create_permanent_unique_task(
        "ext_boltz_paid_invoices", wait_for_paid_invoices
    )
    scheduled_tasks.append(paid_invoices)


__all__ = ["boltz_ext", "boltz_static_files", "boltz_start", "boltz_stop", "db"]
