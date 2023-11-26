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
    tx_hex: str
    txid: str
    script_pub_key: str
    vout_cnt: int
    vout_amount: int


class MempoolApiException(Exception):
    pass


class MempoolBlockHeightException(Exception):
    pass


class MempoolClient:
    def __init__(self, url):
        self._api_url = url
        ws_url = url.replace("https", "wss")
        ws_url = url.replace("http", "ws")
        ws_url += "/ws"
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
            msg = (
                f"{exc.response.status_code} while requesting "
                f"{exc.request.url!r}. message: {exc.response.text}"
            )
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
            output = self.find_output(tx, address)
            if output:
                return output
        return None

    def find_output(self, tx, address: str) -> Optional[LockupData]:
        for i, vout in enumerate(tx["vout"]):
            if vout["scriptpubkey_address"] == address:
                status = "confirmed" if tx["status"]["confirmed"] else "unconfirmed"
                return LockupData(
                    tx_hex=self.get_tx_hex(tx["txid"]),
                    txid=tx["txid"],
                    script_pub_key=vout["scriptpubkey_address"],
                    vout_cnt=i,
                    vout_amount=vout.get("value") or 0,
                    status=status,
                )
        return None

    def get_tx(self, txid: str):
        return self.request(
            "get",
            f"{self._api_url}/tx/{txid}",
            headers={"Content-Type": "application/json"},
        )

    def get_tx_hex(self, txid: str) -> str:
        return self.request(
            "get",
            f"{self._api_url}/tx/{txid}/hex",
            headers={"Content-Type": "text/plain"},
        )["text"]

    def get_txs_from_address(self, address: str):
        return self.request(
            "get",
            f"{self._api_url}/address/{address}/txs",
            headers={"Content-Type": "application/json"},
        )

    async def get_tx_from_txid(self, txid: str, address: str) -> LockupData:
        while True:
            try:
                tx = self.get_tx(txid)
                output = self.find_output(tx, address)
                if output:
                    return output
            except MempoolApiException:
                pass
            await asyncio.sleep(3)

    async def get_tx_from_address(self, address: str) -> LockupData:
        txs = self.get_txs_from_address(address)
        if len(txs) == 0:
            return await self.wait_for_lockup_tx(address)
        lockup_tx = self.find_tx_and_output(txs, address)
        if not lockup_tx:
            return await self.wait_for_lockup_tx(address)
        return lockup_tx

    def get_fees(self) -> int:
        # mempool.space quirk, needed for regtest
        api_url = self._api_url.replace("/v1", "")
        data = self.request(
            "get",
            f"{api_url}/v1/fees/recommended",
            headers={"Content-Type": "application/json"},
        )
        return int(data["halfHourFee"])

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
            raise MempoolBlockHeightException(
                f"current_block_height ({current_block_height}) "
                f"has not yet exceeded ({timeout_block_height})"
            )

    def send_onchain_tx(self, tx_hex: str):
        return self.request(
            "post",
            f"{self._api_url}/tx",
            headers={"Content-Type": "text/plain"},
            content=tx_hex,
        )
