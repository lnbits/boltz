"""boltz_client main module"""

import asyncio
from dataclasses import dataclass
from enum import Enum
from math import ceil, floor
from typing import Optional

import httpx

from .helpers import req_wrap
from .boltz_native import create_boltz_claim_tx, create_boltz_refund_tx
from .onchain import (
    create_key_pair,
    create_preimage,
    validate_address,
)
from .onchain_taproot import (
    is_taproot_swap_data,
    taproot_swap_data_from_response,
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
    id: Optional[str] = None
    hex: Optional[str] = None
    timeoutEta: Optional[str] = None
    timeoutBlockHeight: Optional[int] = None
    failureReason: Optional[str] = None

    @property
    def transactionId(self) -> Optional[str]:
        return self.id

    @property
    def transactionHex(self) -> Optional[str]:
        return self.hex


@dataclass
class BoltzSwapStatusResponse:
    status: str
    failureReason: Optional[str] = None
    zeroConfRejected: Optional[str] = None
    transaction: Optional[dict] = None
    failureDetails: Optional[str] = None


@dataclass
class BoltzSwapResponse:
    id: str
    expectedAmount: int
    bip21: str = ""
    address: str = ""
    redeemScript: Optional[str] = None
    swapTree: Optional[dict] = None
    claimPublicKey: Optional[str] = None
    timeoutBlockHeights: Optional[dict] = None
    acceptZeroConf: bool = False
    timeoutBlockHeight: int = 0
    blindingKey: Optional[str] = None
    referralId: Optional[str] = None

    @property
    def redeem_script(self) -> str:
        return self.redeemScript or taproot_swap_data_from_response(
            self.swapTree, self.claimPublicKey
        )


@dataclass
class BoltzReverseSwapResponse:
    id: str
    invoice: str
    redeemScript: Optional[str] = None
    swapTree: Optional[dict] = None
    refundPublicKey: Optional[str] = None
    refundAddress: Optional[str] = None
    timeoutBlockHeights: Optional[dict] = None
    lockupAddress: str = ""
    timeoutBlockHeight: int = 0
    onchainAmount: int = 0
    blindingKey: Optional[str] = None
    referralId: Optional[str] = None

    @property
    def redeem_script(self) -> str:
        return self.redeemScript or taproot_swap_data_from_response(
            self.swapTree, self.refundPublicKey
        )


@dataclass
class BoltzConfig:
    pairs: list
    network: str = "main"
    network_liquid: str = "liquidv1"
    api_url: str = "https://api.boltz.exchange/v2"
    liquid_esplora_url: str | None = None
    referral_id: str = "dni"


class BoltzClient:
    def __init__(self, config: BoltzConfig, pair: str = "BTC/BTC"):
        self._cfg = config
        self._cfg.api_url = self._normalize_api_url(self._cfg.api_url)
        if pair not in self._cfg.pairs:
            raise BoltzPairException(
                f"invalid pair {pair}, possible pairs: {', '.join(self._cfg.pairs)}"
            )
        self.pair = pair

        if self.pair == "L-BTC/BTC":
            self.network = self._cfg.network_liquid
        else:
            self.network = self._cfg.network
        return None

    @staticmethod
    def _normalize_api_url(api_url: str) -> str:
        api_url = api_url.rstrip("/")
        old_urls = {
            "https://boltz.exchange/api": "https://api.boltz.exchange/v2",
            "https://api.boltz.exchange/api": "https://api.boltz.exchange/v2",
            "https://api.boltz.exchange": "https://api.boltz.exchange/v2",
            "http://localhost:9006": "http://localhost:9006/v2",
        }
        return old_urls.get(api_url, api_url)

    async def init_pairs(self):
        self.pairs = await self.get_pairs()
        self.fees = self.pairs[self.pair]["fees"]
        self.limits = self.pairs[self.pair]["limits"]

    async def request(self, funcname, *args, **kwargs) -> dict:
        try:
            return await req_wrap(funcname, *args, **kwargs)
        except httpx.RequestError as exc:
            msg = f"unreachable: {exc.request.url!r}."
            raise BoltzApiException(f"boltz api connection error: {msg}") from exc
        except httpx.HTTPStatusError as exc:
            try:
                err_msg = exc.response.json()["error"]
            except Exception:
                err_msg = str(exc)
            code = exc.response.status_code
            if code == 404:
                raise BoltzNotFoundException(err_msg) from exc
            msg = f"{code} while requesting {exc.request.url!r}. message: {err_msg}"
            raise BoltzApiException(f"boltz api status error: {msg}") from exc

    async def check_version(self):
        return await self.request(
            "get",
            f"{self._cfg.api_url}/version",
            headers={"Content-Type": "application/json"},
        )

    async def send_onchain_tx(self, rawtw: str) -> str:
        currency = self.pair.split("/")[0]
        data = await self.request(
            "post",
            f"{self._cfg.api_url}/chain/{currency}/transaction",
            headers={"Content-Type": "application/json"},
            json={"hex": rawtw},
        )
        return data["id"]

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

    async def get_pairs(self) -> dict:
        submarine_pairs = await self.request(
            "get",
            f"{self._cfg.api_url}/swap/submarine",
            headers={"Content-Type": "application/json"},
        )
        reverse_pairs = await self.request(
            "get",
            f"{self._cfg.api_url}/swap/reverse",
            headers={"Content-Type": "application/json"},
        )
        pairs = {}
        for pair in self._cfg.pairs:
            base, quote = pair.split("/")
            submarine_pair = submarine_pairs.get(base, {}).get(quote)
            reverse_pair = reverse_pairs.get(quote, {}).get(base)
            if not submarine_pair or not reverse_pair:
                continue
            pairs[pair] = {
                "hash": submarine_pair["hash"],
                "limits": submarine_pair["limits"],
                "fees": {
                    "percentage": reverse_pair["fees"]["percentage"],
                    "percentageSwapIn": submarine_pair["fees"]["percentage"],
                    "minerFees": {
                        "baseAsset": {
                            "normal": submarine_pair["fees"]["minerFees"],
                            "reverse": reverse_pair["fees"]["minerFees"],
                        }
                    },
                },
                "submarine": submarine_pair,
                "reverse": reverse_pair,
            }
        return pairs

    def check_limits(self, amount: int) -> None:
        limits = self.limits
        valid = limits["minimal"] <= amount <= limits["maximal"]
        if not valid:
            raise BoltzLimitException(
                f"Boltz - swap not in boltz limits, amount: {amount}, "
                f"min: {limits['minimal']}, max: {limits['maximal']}"
            )

    async def swap_status(self, boltz_id: str) -> BoltzSwapStatusResponse:
        data = await self.request(
            "get",
            f"{self._cfg.api_url}/swap/{boltz_id}",
            headers={"Content-Type": "application/json"},
        )
        status = BoltzSwapStatusResponse(**data)

        if status.failureReason:
            raise BoltzSwapStatusException(status.failureReason, status.status)

        return status

    async def swap_transaction(self, boltz_id: str) -> BoltzSwapTransactionResponse:
        data = await self.request(
            "get",
            f"{self._cfg.api_url}/swap/submarine/{boltz_id}/transaction",
            headers={"Content-Type": "application/json"},
        )
        res = BoltzSwapTransactionResponse(**data)

        if res.failureReason:
            raise BoltzSwapTransactionException(res.failureReason)

        return res

    async def wait_for_tx(self, boltz_id: str) -> str:
        while True:
            try:
                swap_transaction = await self.swap_transaction(boltz_id)
                assert swap_transaction.transactionHex
                return swap_transaction.transactionHex
            except (ValueError, BoltzApiException, BoltzSwapTransactionException):
                await asyncio.sleep(3)

    async def wait_for_tx_on_status(self, boltz_id: str, zeroconf: bool = True) -> str:
        while True:
            try:
                status = await self.swap_status(boltz_id)
                assert status.transaction
                tx_hex = status.transaction.get("hex")
                assert tx_hex
                if not zeroconf:
                    assert status.status == "transaction.confirmed"
                return tx_hex
            except (BoltzApiException, BoltzSwapStatusException, AssertionError):
                await asyncio.sleep(3)

    async def reverse_swap_transaction(
        self, boltz_id: str
    ) -> BoltzSwapTransactionResponse:
        data = await self.request(
            "get",
            f"{self._cfg.api_url}/swap/reverse/{boltz_id}/transaction",
            headers={"Content-Type": "application/json"},
        )
        res = BoltzSwapTransactionResponse(**data)

        if res.failureReason:
            raise BoltzSwapTransactionException(res.failureReason)

        return res

    def validate_address(self, address: str) -> str:
        try:
            return validate_address(address, self.network, self.pair)
        except ValueError as exc:
            raise BoltzAddressValidationException(exc) from exc

    async def claim_reverse_swap(
        self,
        boltz_id: str,
        lockup_address: str,
        invoice: str,
        receive_address: str,
        privkey_wif: str,
        preimage_hex: str,
        redeem_script_hex: str,
        zeroconf: bool = True,
        blinding_key: Optional[str] = None,
        timeout_block_height: int = 0,
        onchain_amount: int = 0,
    ):
        self.validate_address(receive_address)
        if self.pair != "L-BTC/BTC":
            self.validate_address(lockup_address)
        if not is_taproot_swap_data(redeem_script_hex):
            raise ValueError("Boltz API v2 swap tree data is required")

        await self.wait_for_tx_on_status(boltz_id, zeroconf)
        network = (
            self._cfg.network_liquid if self.pair == "L-BTC/BTC" else self._cfg.network
        )
        transaction = await create_boltz_claim_tx(
            pair=self.pair,
            boltz_id=boltz_id,
            lockup_address=lockup_address,
            invoice=invoice,
            receive_address=receive_address,
            privkey_wif=privkey_wif,
            preimage_hex=preimage_hex,
            taproot_swap_data=redeem_script_hex,
            timeout_block_height=timeout_block_height,
            onchain_amount=onchain_amount,
            blinding_key=blinding_key,
            api_url=self._cfg.api_url,
            network=network,
            esplora_url=(
                self._cfg.liquid_esplora_url if self.pair == "L-BTC/BTC" else None
            ),
            fees=self.get_fee_estimation_claim(),
        )
        return await self.send_onchain_tx(transaction)

    async def refund_swap(
        self,
        boltz_id: str,
        privkey_wif: str,
        lockup_address: str,
        receive_address: str,
        redeem_script_hex: str,
        timeout_block_height: int,
        expected_amount: int = 0,
        blinding_key: Optional[str] = None,
    ) -> str:
        # self.mempool.check_block_height(timeout_block_height)
        self.validate_address(receive_address)
        if self.pair != "L-BTC/BTC":
            self.validate_address(lockup_address)

        if not is_taproot_swap_data(redeem_script_hex):
            raise ValueError("Boltz API v2 swap tree data is required")

        await self.wait_for_tx(boltz_id)
        network = (
            self._cfg.network_liquid if self.pair == "L-BTC/BTC" else self._cfg.network
        )
        transaction = await create_boltz_refund_tx(
            pair=self.pair,
            boltz_id=boltz_id,
            lockup_address=lockup_address,
            receive_address=receive_address,
            privkey_wif=privkey_wif,
            taproot_swap_data=redeem_script_hex,
            timeout_block_height=timeout_block_height,
            expected_amount=expected_amount,
            blinding_key=blinding_key,
            api_url=self._cfg.api_url,
            network=network,
            esplora_url=(
                self._cfg.liquid_esplora_url if self.pair == "L-BTC/BTC" else None
            ),
            fees=self.get_fee_estimation_refund(),
        )
        return await self.send_onchain_tx(transaction)

    async def create_swap(self, payment_request: str) -> tuple[str, BoltzSwapResponse]:
        """create swap and return private key and boltz response"""
        refund_privkey_wif, refund_pubkey_hex = create_key_pair(self.network, self.pair)
        data = await self.request(
            "post",
            f"{self._cfg.api_url}/swap/submarine",
            json={
                "from": self.pair.split("/")[0],
                "to": self.pair.split("/")[1],
                "refundPublicKey": refund_pubkey_hex,
                "invoice": payment_request,
                "pairHash": self.pairs[self.pair]["submarine"]["hash"],
                "referralId": self._cfg.referral_id,
            },
            headers={"Content-Type": "application/json"},
        )
        return refund_privkey_wif, BoltzSwapResponse(**data)

    async def create_reverse_swap(
        self, amount: int = 0
    ) -> tuple[str, str, BoltzReverseSwapResponse]:
        """create reverse swap and return privkey, preimage and boltz response"""
        self.check_limits(amount)
        claim_privkey_wif, claim_pubkey_hex = create_key_pair(self.network, self.pair)
        preimage_hex, preimage_hash = create_preimage()
        data = await self.request(
            "post",
            f"{self._cfg.api_url}/swap/reverse",
            json={
                "from": self.pair.split("/")[1],
                "to": self.pair.split("/")[0],
                "invoiceAmount": amount,
                "preimageHash": preimage_hash,
                "claimPublicKey": claim_pubkey_hex,
                "pairHash": self.pairs[self.pair]["reverse"]["hash"],
                "referralId": self._cfg.referral_id,
            },
            headers={"Content-Type": "application/json"},
        )
        swap = BoltzReverseSwapResponse(**data)
        return claim_privkey_wif, preimage_hex, swap
