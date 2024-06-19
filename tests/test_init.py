import pytest
from fastapi import APIRouter

from .. import boltz_ext, boltz_start, boltz_stop


# just import router and add it to a test router
@pytest.mark.asyncio
async def test_router():
    router = APIRouter()
    router.include_router(boltz_ext)


@pytest.mark.asyncio
async def test_start_and_stop():
    boltz_start()
    boltz_stop()
