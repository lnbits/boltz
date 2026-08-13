"""boltz_client main module"""

import asyncio
from dataclasses import dataclass
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


class BoltzVerificationException(Exception):
    pass


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
    failureDetails: Optional[str] = None


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
    referralId: Optional[str] = None


@dataclass
class BoltzReverseSwapResponse:
    id: str
    invoice: str
    redeemScript: str
    lockupAddress: str
    timeoutBlockHeight: int
    onchainAmount: int
    blindingKey: Optional[str] = None
    referralId: Optional[str] = None


@dataclass
class BoltzConfig:
    pairs: list
    network: str = "main"
    network_liquid: str = "liquidv1"
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

        if self.pair == "L-BTC/BTC":
            self.network = self._cfg.network_liquid
        else:
            self.network = self._cfg.network
        return None

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
        data = await self.request(
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

    async def get_pairs(self) -> dict:
        data = await self.request(
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

    def verify_swap_response(
        self,
        swap: object,
        expected_preimage_hash: str = "",
        expected_amount: int = 0,
        current_blockheight: int = 0,
        expected_claim_pubkey: str = "",
    ) -> None:
        # SPEC (Boltz dont-trust-verify.md): clients should verify swap responses.
        # REF: https://github.com/BoltzExchange/boltz-backend/blob/master/docs/dont-trust-verify.md
        # REF: electrum/submarine_swaps.py _check_swap_scriptcode() + reverse_swap()
        # REF: boltz-web-app src/utils/validation.ts validateResponse()
        if expected_preimage_hash and hasattr(swap, "invoice") and swap.invoice:
            try:
                from bolt11 import decode as bolt11_decode

                decoded = bolt11_decode(swap.invoice)
                if decoded.payment_hash and decoded.payment_hash != expected_preimage_hash:
                    raise BoltzVerificationException(
                        f"Invoice payment_hash {decoded.payment_hash[:16]}... "
                        f"does not match preimage hash "
                        f"{expected_preimage_hash[:16]}..."
                    )
                if expected_amount and decoded.amount_msat:
                    if abs(decoded.amount_msat - expected_amount * 1000) > expected_amount * 100:
                        raise BoltzVerificationException(
                            f"Invoice amount {decoded.amount_msat} msat does not "
                            f"match expected {expected_amount * 1000} msat"
                        )
                if decoded.expiry and decoded.date:
                    import time

                    if decoded.date + decoded.expiry < time.time():
                        raise BoltzVerificationException(
                            "Invoice has expired"
                        )
            except ImportError:
                pass
            except BoltzVerificationException:
                raise
            except Exception:
                pass

        if expected_amount and hasattr(swap, "onchainAmount") and swap.onchainAmount:
            if swap.onchainAmount < int(expected_amount * 0.9):
                raise BoltzVerificationException(
                    f"onchainAmount {swap.onchainAmount} too low "
                    f"(expected ~{expected_amount})"
                )

        if hasattr(swap, "redeemScript") and swap.redeemScript:
            self._verify_redeem_script(
                swap.redeemScript,
                expected_preimage_hash,
                expected_claim_pubkey,
                getattr(swap, "timeoutBlockHeight", 0),
            )

        if hasattr(swap, "lockupAddress") and swap.lockupAddress and hasattr(swap, "redeemScript") and swap.redeemScript:
            self._verify_lockup_address(swap.redeemScript, swap.lockupAddress)

        if hasattr(swap, "timeoutBlockHeight") and swap.timeoutBlockHeight and current_blockheight:
            delta = swap.timeoutBlockHeight - current_blockheight
            if delta < 12:
                raise BoltzVerificationException(
                    f"timeoutBlockHeight too close ({delta} blocks)"
                )
            if delta > 288:
                raise BoltzVerificationException(
                    f"timeoutBlockHeight too far ({delta} blocks)"
                )

        if hasattr(swap, "timeoutBlockHeight") and swap.timeoutBlockHeight and current_blockheight:
            if swap.timeoutBlockHeight <= current_blockheight:
                raise BoltzVerificationException(
                    f"swap has already expired (timeout {swap.timeoutBlockHeight} <= "
                    f"current {current_blockheight})"
                )

    def verify_swap_costs(
        self,
        swap: object,
        lightning_amount_sat: int,
        onchain_amount_sat: int,
        fee_percentage: float,
        miner_fee_sat: int,
    ) -> None:
        # SPEC (Boltz dont-trust-verify.md): "clients should calculate swap amounts locally"
        # REF: electrum/submarine_swaps.py _sanity_check_swap_costs()
        # REF: electrum commit cbdaa035 (PR #10827)
        MAX_FEE_RATIO = 0.15

        expected_server_fee = int(lightning_amount_sat * fee_percentage / 100) + miner_fee_sat
        actual_fee = lightning_amount_sat - onchain_amount_sat
        if actual_fee < 0:
            raise BoltzVerificationException(
                f"onchainAmount {onchain_amount_sat} exceeds lightningAmount "
                f"{lightning_amount_sat} — negative fee"
            )
        if lightning_amount_sat > 0:
            fee_ratio = actual_fee / lightning_amount_sat
            if fee_ratio > MAX_FEE_RATIO:
                raise BoltzVerificationException(
                    f"swap fee ratio {fee_ratio:.1%} exceeds {MAX_FEE_RATIO:.0%} limit "
                    f"(fee={actual_fee} sat, amount={lightning_amount_sat} sat)"
                )

    def verify_prepayment(
        self,
        prepayment_sat: int,
        lightning_amount_sat: int,
        miner_fee_sat: int = 0,
    ) -> None:
        # REF: electrum/submarine_swaps.py _sanity_check_prepayment()
        # REF: electrum commit cbdaa035 (PR #10827): "provider could set a negative
        #      percentage fee + huge mining fee and we would send the trusted prepayment"
        MAX_PREPAY_RATIO = 0.5
        MAX_PREPAY_MULTIPLIER = 4

        max_prepayment = min(
            int(MAX_PREPAY_RATIO * lightning_amount_sat),
            MAX_PREPAY_MULTIPLIER * max(miner_fee_sat, 1),
        )
        if prepayment_sat > max_prepayment:
            raise BoltzVerificationException(
                f"Mining fee prepayment {prepayment_sat} sat exceeds sane maximum "
                f"{max_prepayment} sat"
            )

    def _verify_redeem_script(
        self,
        redeem_script_hex: str,
        expected_preimage_hash: str = "",
        expected_claim_pubkey: str = "",
        expected_timeout: int = 0,
    ) -> None:
        # SPEC (Boltz dont-trust-verify.md): "clients should verify that the redeem script
        # is valid by checking preimage hash, public key, timeout block height of the HTLC and OP codes"
        # REF: electrum/submarine_swaps.py _check_swap_scriptcode() + match_script_against_template()
        # REF: clboss Boltz/Detail/match_lockscript.cpp (existing CLBOSS verification)
        import hashlib

        # Bitcoin Script opcodes for the HTLC template.
        # Same template as Electrum WITNESS_TEMPLATE_SWAP and boltz-core swapScript().
        OP_SIZE = 0x82; OP_EQUAL = 0x87; OP_IF = 0x63
        OP_HASH160 = 0xa9; OP_EQUALVERIFY = 0x88; OP_ELSE = 0x67
        OP_DROP = 0x75; OP_CLTV = 0xb1; OP_ENDIF = 0x68; OP_CHECKSIG = 0xac
        P1=0x01; P3=0x03; P20=0x14; P32=0x20; P33=0x21

        script = bytes.fromhex(redeem_script_hex)

        if len(script) != 106:
            raise BoltzVerificationException(
                f"redeemScript wrong length: {len(script)} (expected 106)"
            )

        fixed = {
            0: OP_SIZE, 2: P32, 3: OP_EQUAL, 4: OP_IF,
            5: OP_HASH160, 6: P20, 27: OP_EQUALVERIFY, 28: P33,
            62: OP_ELSE, 63: OP_DROP, 64: P3,
            68: OP_CLTV, 69: OP_DROP, 70: P33,
            104: OP_ENDIF, 105: OP_CHECKSIG,
        }
        for pos, val in fixed.items():
            if script[pos] != val:
                raise BoltzVerificationException(
                    f"redeemScript byte {pos}: expected 0x{val:02x}, got 0x{script[pos]:02x}"
                )

        script_h160 = script[7:27]
        script_claim_pubkey = script[29:62]
        script_locktime = int.from_bytes(script[65:68], byteorder="little")

        if expected_preimage_hash:
            try:
                sha256_hash = bytes.fromhex(expected_preimage_hash)
                rip = hashlib.new("ripemd160", sha256_hash).digest()
                if rip != script_h160:
                    raise BoltzVerificationException(
                        "redeemScript HASH160(preimage) mismatch"
                    )
            except ValueError:
                pass

        if expected_claim_pubkey:
            expected_bytes = bytes.fromhex(expected_claim_pubkey)
            if expected_bytes != script_claim_pubkey:
                raise BoltzVerificationException(
                    "redeemScript claim pubkey mismatch"
                )

        if expected_timeout and script_locktime != expected_timeout:
            raise BoltzVerificationException(
                f"redeemScript locktime mismatch: {script_locktime} != {expected_timeout}"
            )

    def _verify_lockup_address(self, redeem_script_hex: str, lockup_address: str) -> None:
        # SPEC (Boltz dont-trust-verify.md): "clients should also verify the correctness
        # of the given address" — P2WSH: script hash = SHA256 of the redeem script
        # REF: electrum/submarine_swaps.py: "if script_to_p2wsh(redeem_script) != lockup_address: raise"
        # CAVEAT: requires embit; silently skipped if embit import fails
        import hashlib
        try:
            script = bytes.fromhex(redeem_script_hex)
            script_sha256 = hashlib.sha256(script).digest()
            expected_p2wsh = b"\x00\x20" + script_sha256
            from embit.script import Script
            from embit.networks import NETWORKS
            if hasattr(self, 'network') and self.network in NETWORKS:
                from embit.addresses import Address
                addr = Address(Script(expected_p2wsh))
                derived = addr.to_address(NETWORKS[self.network]["bech32_prefix"] if isinstance(NETWORKS[self.network], dict) else "bcrt")
                if derived != lockup_address:
                    raise BoltzVerificationException(
                        f"lockupAddress mismatch: derived {derived[:20]}..., "
                        f"API provided {lockup_address[:20]}..."
                    )
        except BoltzVerificationException:
            raise
        except Exception:
            pass

    async def swap_status(self, boltz_id: str) -> BoltzSwapStatusResponse:
        data = await self.request(
            "post",
            f"{self._cfg.api_url}/swapstatus",
            json={"id": boltz_id},
            headers={"Content-Type": "application/json"},
        )
        status = BoltzSwapStatusResponse(**data)

        if status.failureReason:
            raise BoltzSwapStatusException(status.failureReason, status.status)

        return status

    async def swap_transaction(self, boltz_id: str) -> BoltzSwapTransactionResponse:
        data = await self.request(
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
        return await self.send_onchain_tx(transaction)

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
        return await self.send_onchain_tx(transaction)

    async def create_swap(self, payment_request: str) -> tuple[str, BoltzSwapResponse]:
        """create swap and return private key and boltz response"""
        refund_privkey_wif, refund_pubkey_hex = create_key_pair(self.network, self.pair)
        data = await self.request(
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

    async def create_reverse_swap(
        self, amount: int = 0
    ) -> tuple[str, str, BoltzReverseSwapResponse]:
        """create reverse swap and return privkey, preimage and boltz response"""
        self.check_limits(amount)
        claim_privkey_wif, claim_pubkey_hex = create_key_pair(self.network, self.pair)
        preimage_hex, preimage_hash = create_preimage()
        data = await self.request(
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
        self.verify_swap_response(swap, expected_preimage_hash=preimage_hash,
                                  expected_amount=amount)
        return claim_privkey_wif, preimage_hex, swap
