""" boltz_client mempool module """

import asyncio
import json
from dataclasses import dataclass
from typing import Optional

import httpx
from websockets.client import connect
from websockets.exceptions import ConnectionClosed

from .helpers import req_wrap


@dataclass
class LockupData:
    status: str
    txid: str
    vout_cnt: int
    vout_amount: int


class MempoolApiException(Exception):
    pass


class MempoolBlockHeightException(Exception):
    pass


class MempoolClient:
    def __init__(self, url, ws_url):
        self._api_url = url
        self._ws_url = ws_url
        # just check if mempool is available
        self.get_blockheight()

    def request(self, funcname, *args, **kwargs) -> dict:
        try:
            return req_wrap(funcname, *args, **kwargs)
        except httpx.RequestError as exc:
            msg = f"unreachable: {exc.request.url!r}."
            raise MempoolApiException(f"mempool api connection error: {msg}") from exc
        except httpx.HTTPStatusError as exc:
            msg = f"{exc.response.status_code} while requesting {exc.request.url!r}. message: {exc.response.text}"
            raise MempoolApiException(f"mempool api status error: {msg}") from exc

    async def wait_for_websocket_message(self, send, message_key):
        async for websocket in connect(self._ws_url):
            try:
                await websocket.send(json.dumps({"action": "want", "data": ["blocks"]}))
                await websocket.send(json.dumps(send))
                async for raw in websocket:
                    message = json.loads(raw)
                    if message_key in message:
                        return message.get(message_key)
            except ConnectionClosed:
                continue

    async def wait_for_one_websocket_message(self, send):
        async with connect(self._ws_url) as websocket:
            await websocket.send(
                json.dumps({"action": "want", "data": ["blocks", "mempool-blocks"]})
            )
            await websocket.send(json.dumps(send))
            raw = await asyncio.wait_for(websocket.recv(), timeout=10)
            return json.loads(raw) if raw else None

    async def wait_for_tx_confirmed(self, txid: str):
        return await self.wait_for_websocket_message({"track-tx": txid}, "txConfirmed")

    async def wait_for_lockup_tx(self, address: str) -> LockupData:
        message = await self.wait_for_websocket_message(
            {"track-address": address}, "address-transactions"
        )
        if not message:
            # restart
            return await self.wait_for_lockup_tx(address)
        lockup_tx = self.find_tx_and_output(message, address)
        if not lockup_tx:
            # restart
            return await self.wait_for_lockup_tx(address)
        return lockup_tx

    def find_tx_and_output(self, txs, address: str) -> Optional[LockupData]:
        if len(txs) == 0:
            return None
        for tx in txs:
            for i, vout in enumerate(tx["vout"]):
                if vout["scriptpubkey_address"] == address:
                    status = "confirmed" if tx["status"]["confirmed"] else "unconfirmed"
                    return LockupData(
                        txid=tx["txid"],
                        vout_cnt=i,
                        vout_amount=vout["value"],
                        status=status,
                    )
        return None

    def get_txs_from_address(self, address: str):
        return self.request(
            "get",
            f"{self._api_url}/address/{address}/txs",
            headers={"Content-Type": "application/json"},
        )

    async def get_tx_from_address(self, address: str) -> LockupData:
        txs = self.get_txs_from_address(address)
        if len(txs) == 0:
            return await self.wait_for_lockup_tx(address)
        lockup_tx = self.find_tx_and_output(txs, address)
        if not lockup_tx:
            return await self.wait_for_lockup_tx(address)
        return lockup_tx

    def get_fee_estimation(self) -> int:
        # TODO: hardcoded maximum tx size, in the future we try to get the size of the tx via embit
        # we need a function like Transaction.vsize()
        tx_size_vbyte = 200
        mempool_fees = self.get_fees()
        return mempool_fees * tx_size_vbyte

    def get_fees(self) -> int:
        data = self.request(
            "get",
            f"{self._api_url}/v1/fees/recommended",
            headers={"Content-Type": "application/json"},
        )
        return int(data["economyFee"])

    def get_blockheight(self) -> int:
        data = self.request(
            "get",
            f"{self._api_url}/blocks/tip/height",
            headers={"Content-Type": "text/plain"},
        )
        return int(data["text"])

    def check_block_height(self, timeout_block_height: int) -> None:
        current_block_height = self.get_blockheight()
        if current_block_height < timeout_block_height:
            msg = f"current_block_height ({current_block_height}) has not yet exceeded ({timeout_block_height})"
            raise MempoolBlockHeightException(msg)

    def send_onchain_tx(self, tx_hex: str):
        return self.request(
            "post",
            f"{self._api_url}/tx",
            headers={"Content-Type": "text/plain"},
            content=tx_hex,
        )
