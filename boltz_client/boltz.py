""" boltz_client main module """

import asyncio
from dataclasses import dataclass
from typing import Optional

import httpx
from embit.transaction import Transaction, TransactionError

from .helpers import req_wrap
from .mempool import MempoolClient
from .onchain import create_claim_tx, create_key_pair, create_preimage, create_refund_tx


class BoltzLimitException(Exception):
    pass


class BoltzApiException(Exception):
    pass


class BoltzNotFoundException(Exception):
    pass


class BoltzSwapStatusException(Exception):
    def __init__(self, message: str, status: str):
        self.message = message
        self.status = status


class BoltzSwapTransactionException(Exception):
    def __init__(self, message: str):
        self.message = message


@dataclass
class BoltzSwapTransactionResponse:
    transactionHex: Optional[str] = None
    timeoutBlockHeight: Optional[str] = None
    failureReason: Optional[str] = None


@dataclass
class BoltzSwapStatusResponse:
    status: str
    failureReason: Optional[str] = None
    zeroConfRejected: Optional[str] = None
    transaction: Optional[dict] = None


@dataclass
class BoltzSwapResponse:
    id: str
    bip21: str
    address: str
    redeemScript: str
    acceptZeroConf: bool
    expectedAmount: int
    timeoutBlockHeight: int


@dataclass
class BoltzReverseSwapResponse:
    id: str
    invoice: str
    redeemScript: str
    lockupAddress: str
    timeoutBlockHeight: int
    onchainAmount: int


@dataclass
class BoltzConfig:
    network: str = "main"
    api_url: str = "https://boltz.exchange/api"
    mempool_url: str = "https://mempool.space/api/v1"
    mempool_ws_url: str = "wss://mempool.space/api/v1/ws"
    referral_id: str = "dni"


class BoltzClient:
    def __init__(self, config: BoltzConfig):
        self._cfg = config
        self.limit_minimal = 0
        self.limit_maximal = 0
        self.fee_percentage = 0
        self.set_limits()
        self.mempool = MempoolClient(self._cfg.mempool_url, self._cfg.mempool_ws_url)

    def request(self, funcname, *args, **kwargs) -> dict:
        try:
            return req_wrap(funcname, *args, **kwargs)
        except httpx.RequestError as exc:
            msg = f"unreachable: {exc.request.url!r}."
            raise BoltzApiException(f"boltz api connection error: {msg}") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise BoltzNotFoundException(exc.response.json()["error"]) from exc
            msg = f"{exc.response.status_code} while requesting {exc.request.url!r}. message: {exc.response.json()['error']}"
            raise BoltzApiException(f"boltz api status error: {msg}") from exc

    def check_version(self):
        return self.request(
            "get",
            f"{self._cfg.api_url}/version",
            headers={"Content-Type": "application/json"},
        )

    def get_fee_estimation(self, feerate: Optional[int]) -> int:
        # TODO: hardcoded maximum tx size, in the future we try to get the size of the
        # tx via embit we need a function like Transaction.vsize()
        tx_size_vbyte = 200
        mempool_fees = feerate if feerate else self.mempool.get_fees()
        return mempool_fees * tx_size_vbyte

    def set_limits(self) -> None:
        data = self.request(
            "get",
            f"{self._cfg.api_url}/getpairs",
            headers={"Content-Type": "application/json"},
        )
        pair = data["pairs"]["BTC/BTC"]
        limits = pair["limits"]
        fees = pair["fees"]
        self.limit_maximal = limits["maximal"]
        self.limit_minimal = limits["minimal"]
        self.fee_percentage = fees["percentage"]

    def check_limits(self, amount: int) -> None:
        valid = self.limit_minimal <= amount <= self.limit_maximal
        if not valid:
            raise BoltzLimitException(
                f"Boltz - swap not in boltz limits, amount: {amount}, "
                f"min: {self.limit_minimal}, max: {self.limit_maximal}"
            )

    def swap_status(self, boltz_id: str) -> BoltzSwapStatusResponse:
        data = self.request(
            "post",
            f"{self._cfg.api_url}/swapstatus",
            json={"id": boltz_id},
            headers={"Content-Type": "application/json"},
        )
        status = BoltzSwapStatusResponse(**data)

        if status.failureReason:
            raise BoltzSwapStatusException(status.failureReason, status.status)

        return status

    def swap_transaction(self, boltz_id: str) -> BoltzSwapTransactionResponse:
        data = self.request(
            "post",
            f"{self._cfg.api_url}/getswaptransaction",
            json={"id": boltz_id},
            headers={"Content-Type": "application/json"},
        )
        res = BoltzSwapTransactionResponse(**data)

        if res.failureReason:
            raise BoltzSwapTransactionException(res.failureReason)

        return res

    async def wait_for_txid(self, boltz_id: str) -> str:
        while True:
            try:
                tx_hex = self.swap_transaction(boltz_id)
                tx = Transaction.from_string(tx_hex.transactionHex)
                return tx.txid().hex()
            except (BoltzApiException, BoltzSwapTransactionException, TransactionError):
                await asyncio.sleep(5)

    async def wait_for_txid_on_status(self, boltz_id: str) -> str:
        while True:
            try:
                status = self.swap_status(boltz_id)
                assert status.transaction
                txid = status.transaction.get("id")
                assert txid
                return txid
            except (BoltzApiException, BoltzSwapStatusException, AssertionError):
                await asyncio.sleep(5)

    async def claim_reverse_swap(
        self,
        boltz_id: str,
        lockup_address: str,
        receive_address: str,
        privkey_wif: str,
        preimage_hex: str,
        redeem_script_hex: str,
        zeroconf: bool = False,
        feerate: Optional[int] = None,
    ):
        lockup_txid = await self.wait_for_txid_on_status(boltz_id)
        lockup_tx = await self.mempool.get_tx_from_txid(lockup_txid, lockup_address)

        if not zeroconf and lockup_tx.status != "confirmed":
            await self.mempool.wait_for_tx_confirmed(lockup_tx.txid)

        txid, transaction = create_claim_tx(
            lockup_tx=lockup_tx,
            receive_address=receive_address,
            privkey_wif=privkey_wif,
            redeem_script_hex=redeem_script_hex,
            preimage_hex=preimage_hex,
            fees=self.get_fee_estimation(feerate),
        )

        self.mempool.send_onchain_tx(transaction)
        return txid

    async def refund_swap(
        self,
        boltz_id: str,
        privkey_wif: str,
        lockup_address: str,
        receive_address: str,
        redeem_script_hex: str,
        timeout_block_height: int,
        feerate: Optional[int] = None,
    ) -> str:
        self.mempool.check_block_height(timeout_block_height)
        lockup_txid = await self.wait_for_txid(boltz_id)
        lockup_tx = await self.mempool.get_tx_from_txid(lockup_txid, lockup_address)
        txid, transaction = create_refund_tx(
            lockup_tx=lockup_tx,
            privkey_wif=privkey_wif,
            receive_address=receive_address,
            redeem_script_hex=redeem_script_hex,
            timeout_block_height=timeout_block_height,
            fees=self.get_fee_estimation(feerate),
        )

        self.mempool.send_onchain_tx(transaction)
        return txid

    def create_swap(self, payment_request: str) -> tuple[str, BoltzSwapResponse]:
        """create swap and return private key and boltz response"""
        refund_privkey_wif, refund_pubkey_hex = create_key_pair(self._cfg.network)
        data = self.request(
            "post",
            f"{self._cfg.api_url}/createswap",
            json={
                "type": "submarine",
                "pairId": "BTC/BTC",
                "orderSide": "sell",
                "refundPublicKey": refund_pubkey_hex,
                "invoice": payment_request,
                "referralId": self._cfg.referral_id,
            },
            headers={"Content-Type": "application/json"},
        )
        return refund_privkey_wif, BoltzSwapResponse(**data)

    def create_reverse_swap(
        self, amount: int = 0
    ) -> tuple[str, str, BoltzReverseSwapResponse]:
        """create reverse swap and return privkey, preimage and boltz response"""
        self.check_limits(amount)
        claim_privkey_wif, claim_pubkey_hex = create_key_pair(self._cfg.network)
        preimage_hex, preimage_hash = create_preimage()
        data = self.request(
            "post",
            f"{self._cfg.api_url}/createswap",
            json={
                "type": "reversesubmarine",
                "pairId": "BTC/BTC",
                "orderSide": "buy",
                "invoiceAmount": amount,
                "preimageHash": preimage_hash,
                "claimPublicKey": claim_pubkey_hex,
                "referralId": self._cfg.referral_id,
            },
            headers={"Content-Type": "application/json"},
        )
        swap = BoltzReverseSwapResponse(**data)
        return claim_privkey_wif, preimage_hex, swap
