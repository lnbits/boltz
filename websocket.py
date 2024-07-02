import asyncio
import json

from lnbits.settings import settings
from loguru import logger
from websockets.client import connect

from .crud import get_or_create_boltz_settings

ws_receive_queue: asyncio.Queue[dict] = asyncio.Queue()
ws_send_queue: asyncio.Queue[dict] = asyncio.Queue()


def subscribe_to_swap_updates(boltz_id: str):
    event = {
        "op": "subscribe",
        "channel": "swap.update",
        "args": [boltz_id],
    }
    logger.debug(f"Subscribing to swap updates for {boltz_id}")
    ws_send_queue.put_nowait(event)


async def consumer_handler(websocket):
    async for message in websocket:
        logger.debug(f"Received message: {message}")
        ws_receive_queue.put_nowait(json.loads(message))


async def producer_handler(websocket):
    while settings.lnbits_running:
        message = await ws_send_queue.get()
        await websocket.send(json.dumps(message))


async def websocket_handler():
    settings = await get_or_create_boltz_settings()
    uri = settings.boltz_url.replace("https", "wss").replace("http", "ws")
    logger.info(f"Boltz: Connecting to {uri}...")
    async with connect(uri) as websocket:
        logger.info(f"Boltz: Connected to {uri}")
        consumer_task = asyncio.create_task(consumer_handler(websocket))
        producer_task = asyncio.create_task(producer_handler(websocket))
        _, pending = await asyncio.wait(
            [consumer_task, producer_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

    raise Exception("websocket_handler unexpectedly finished")
