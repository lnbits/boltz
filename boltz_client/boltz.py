""" boltz_client main module """

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from math import ceil, floor
from typing import Optional

import httpx

from .helpers import req_wrap
from .onchain import (
    create_claim_tx,
    create_key_pair,
    create_preimage,
    create_refund_tx,
    validate_address,
)


class SwapDirection(str, Enum):
    send = "send"
    receive = "receive"


class BoltzLimitException(Exception):
    pass


class BoltzApiException(Exception):
    pass


class BoltzAddressValidationException(Exception):
    pass


class BoltzNotFoundException(Exception):
    pass


class BoltzPairException(Exception):
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
    transactionId: Optional[str] = None
    transactionHex: Optional[str] = None
    timeoutEta: Optional[str] = None
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
    blindingKey: Optional[str] = None


@dataclass
class BoltzReverseSwapResponse:
    id: str
    invoice: str
    redeemScript: str
    lockupAddress: str
    timeoutBlockHeight: int
    onchainAmount: int
    blindingKey: Optional[str] = None


@dataclass
class BoltzConfig:
    network: str = "main"
    network_liquid: str = "liquidv1"
    pairs: list = field(default_factory=lambda: ["BTC/BTC", "L-BTC/BTC"])
    api_url: str = "https://boltz.exchange/api"
    referral_id: str = "dni"


class BoltzClient:
    def __init__(self, config: BoltzConfig, pair: str = "BTC/BTC"):
        self._cfg = config
        if pair not in self._cfg.pairs:
            raise BoltzPairException(
                f"invalid pair {pair}, possible pairs: {', '.join(self._cfg.pairs)}"
            )
        self.pair = pair
        self.pairs = self.get_pairs()
        self.fees = self.pairs[self.pair]["fees"]
        self.limits = self.pairs[self.pair]["limits"]

        if self.pair == "L-BTC/BTC":
            self.network = self._cfg.network_liquid
        else:
            self.network = self._cfg.network

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

    def send_onchain_tx(self, rawtw: str) -> str:
        data = self.request(
            "post",
            f"{self._cfg.api_url}/broadcasttransaction",
            headers={"Content-Type": "application/json"},
            json={"currency": self.pair.split("/")[0], "transactionHex": rawtw},
        )
        return data["transactionId"]

    def add_reverse_swap_fees(self, amount: int) -> int:
        rev = self.fees["minerFees"]["baseAsset"]["reverse"]
        fee = rev["claim"] + rev["lockup"]
        percent = self.fees["percentage"]
        return ceil((amount + fee) / (1 - (percent / 100)))

    def substract_swap_fees(self, amount: int) -> int:
        fee = self.fees["minerFees"]["baseAsset"]["normal"]
        percent = self.fees["percentageSwapIn"]
        return floor((amount - fee) / (1 + (percent / 100)))

    def get_fee_estimation_claim(self) -> int:
        return self.fees["minerFees"]["baseAsset"]["reverse"]["claim"]

    def get_fee_estimation_refund(self) -> int:
        return self.fees["minerFees"]["baseAsset"]["normal"]

    def get_pairs(self) -> dict:
        data = self.request(
            "get",
            f"{self._cfg.api_url}/getpairs",
            headers={"Content-Type": "application/json"},
        )
        return data["pairs"]

    def check_limits(self, amount: int) -> None:
        limits = self.limits
        valid = limits["minimal"] <= amount <= limits["maximal"]
        if not valid:
            raise BoltzLimitException(
                f"Boltz - swap not in boltz limits, amount: {amount}, "
                f"min: {limits['minimal']}, max: {limits['maximal']}"
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

    async def wait_for_tx(self, boltz_id: str) -> str:
        while True:
            try:
                swap_transaction = self.swap_transaction(boltz_id)
                assert swap_transaction.transactionHex
                return swap_transaction.transactionHex
            except (ValueError, BoltzApiException, BoltzSwapTransactionException):
                await asyncio.sleep(3)

    async def wait_for_tx_on_status(self, boltz_id: str, zeroconf: bool = True) -> str:
        while True:
            try:
                status = self.swap_status(boltz_id)
                assert status.transaction
                txHex = status.transaction.get("hex")
                assert txHex
                if not zeroconf:
                    assert status.status == "transaction.confirmed"
                return txHex
            except (BoltzApiException, BoltzSwapStatusException, AssertionError):
                await asyncio.sleep(3)

    def validate_address(self, address: str) -> str:
        try:
            return validate_address(address, self.network, self.pair)
        except ValueError as exc:
            raise BoltzAddressValidationException(exc) from exc

    async def claim_reverse_swap(
        self,
        boltz_id: str,
        lockup_address: str,
        receive_address: str,
        privkey_wif: str,
        preimage_hex: str,
        redeem_script_hex: str,
        zeroconf: bool = True,
        blinding_key: Optional[str] = None,
    ):
        self.validate_address(receive_address)
        self.validate_address(lockup_address)
        lockup_rawtx = await self.wait_for_tx_on_status(boltz_id, zeroconf)

        transaction = create_claim_tx(
            lockup_address=lockup_address,
            lockup_rawtx=lockup_rawtx,
            receive_address=receive_address,
            privkey_wif=privkey_wif,
            redeem_script_hex=redeem_script_hex,
            preimage_hex=preimage_hex,
            pair=self.pair,
            blinding_key=blinding_key,
            fees=self.get_fee_estimation_claim(),
        )
        return self.send_onchain_tx(transaction)

    async def refund_swap(
        self,
        boltz_id: str,
        privkey_wif: str,
        lockup_address: str,
        receive_address: str,
        redeem_script_hex: str,
        timeout_block_height: int,
        blinding_key: Optional[str] = None,
    ) -> str:
        # self.mempool.check_block_height(timeout_block_height)
        self.validate_address(receive_address)
        self.validate_address(lockup_address)

        lockup_rawtx = await self.wait_for_tx(boltz_id)
        transaction = create_refund_tx(
            lockup_address=lockup_address,
            lockup_rawtx=lockup_rawtx,
            privkey_wif=privkey_wif,
            receive_address=receive_address,
            redeem_script_hex=redeem_script_hex,
            timeout_block_height=timeout_block_height,
            pair=self.pair,
            blinding_key=blinding_key,
            fees=self.get_fee_estimation_refund(),
        )
        return self.send_onchain_tx(transaction)

    def create_swap(self, payment_request: str) -> tuple[str, BoltzSwapResponse]:
        """create swap and return private key and boltz response"""
        refund_privkey_wif, refund_pubkey_hex = create_key_pair(self.network, self.pair)
        data = self.request(
            "post",
            f"{self._cfg.api_url}/createswap",
            json={
                "type": "submarine",
                "pairId": self.pair,
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
        claim_privkey_wif, claim_pubkey_hex = create_key_pair(self.network, self.pair)
        preimage_hex, preimage_hash = create_preimage()
        data = self.request(
            "post",
            f"{self._cfg.api_url}/createswap",
            json={
                "type": "reversesubmarine",
                "pairId": self.pair,
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
