import asyncio
import calendar
import datetime
from typing import Awaitable

from boltz_client_bindings import (
    BtcSwapScript,
    Client,
    CreateSubmarineResponse,
    new_keys,
)
from boltz_client_bindings import validate_address as validate
from lnbits.core.crud import get_wallet
from lnbits.core.services import fee_reserve_total, pay_invoice
from loguru import logger

from .crud import get_or_create_boltz_settings
from .models import ReverseSubmarineSwap


async def boltz_validate_address(asset: str, address: str) -> bool:
    settings = await get_or_create_boltz_settings()
    try:
        return validate(asset, settings.boltz_network, address)
    except Exception as exc:
        logger.warning(f"Error validating address: {exc}")
        return False

async def create_boltz_client() -> Client:
    settings = await get_or_create_boltz_settings()
    return Client(settings.boltz_url, "lnbits")


async def boltz_get_submarine_pairs() -> dict:
    client = await create_boltz_client()
    pairs = client.get_submarine_pairs()
    return pairs.to_dict()


async def boltz_get_status(swap_id: str) -> dict:
    # client = await create_boltz_client()
    return {}
    # status = await client.get_swap(swap_id)
    # return status.to_dict()


async def boltz_create_swap(invoice: str) -> tuple[str, CreateSubmarineResponse]:
    private_key, public_key = new_keys()
    client = await create_boltz_client()
    swap = client.create_submarine_swap(
        "BTC",
        "BTC",
        invoice,
        public_key,
    )
    script = BtcSwapScript.from_submarine_response(
        swap,
        public_key,
    )
    print("is submarine", script.is_submarine())
    return bytes(private_key).hex(), swap



async def boltz_refund_swap(swap: ReverseSubmarineSwap):
    pass
    # client = await create_boltz_client()
    # await client.refund_swap(
    #     boltz_id=swap.boltz_id,
    #     privkey_wif=swap.refund_privkey,
    #     lockup_address=swap.address,
    #     receive_address=swap.refund_address,
    #     redeem_script_hex=swap.redeem_script,
    #     timeout_block_height=swap.timeout_block_height,
    #     # feerate=swap.feerate_value if swap.feerate else None,
    #     blinding_key=swap.blinding_key,
    # )


async def check_balance(data) -> bool:
    # check if we can pay the invoice before we create the actual swap on boltz
    amount_msat = data.amount * 1000
    fee_reserve_msat = fee_reserve_total(amount_msat)
    wallet = await get_wallet(data.wallet)
    assert wallet
    if wallet.balance_msat - fee_reserve_msat < amount_msat:
        return False
    return True


def get_timestamp():
    date = datetime.datetime.now()
    return calendar.timegm(date.utctimetuple())


async def execute_reverse_swap(client: Client, swap: ReverseSubmarineSwap):
    # claim_task is watching for the lockup transaction to arrive / confirm
    # and if the lockup is there, claim the onchain revealing preimage for hold invoice
    claim_task = asyncio.create_task(
        client.claim_reverse_swap(
            boltz_id=swap.boltz_id,
            privkey_wif=swap.claim_privkey,
            preimage_hex=swap.preimage,
            lockup_address=swap.lockup_address,
            receive_address=swap.onchain_address,
            redeem_script_hex=swap.redeem_script,
            zeroconf=swap.instant_settlement,
            # feerate=swap.feerate_value if swap.feerate else None,
            blinding_key=swap.blinding_key,
        )
    )
    # pay_task is paying the hold invoice which gets held until you reveal
    # your preimage when claiming your onchain funds
    pay_task = pay_invoice_and_update_status(
        swap.id,
        claim_task,
        pay_invoice(
            wallet_id=swap.wallet,
            payment_request=swap.invoice,
            description=(
                f"reverse swapped {swap.asset}: {swap.onchain_amount} sats on "
                "boltz.exchange"
            ),
            extra={"tag": "boltz", "swap_id": swap.id, "reverse": True},
        ),
    )

    # they need to run be concurrently, because else pay_task will lock the eventloop
    # and claim_task will not be executed. the lockup transaction can only happen after
    # you pay the invoice, which cannot be redeemed immediatly -> hold invoice
    # after getting the lockup transaction, you can claim the onchain funds revealing
    # the preimage for boltz to redeem the hold invoice
    asyncio.gather(claim_task, pay_task)


def pay_invoice_and_update_status(
    swap_id: str, wstask: asyncio.Task, awaitable: Awaitable
) -> asyncio.Task:
    async def _pay_invoice(awaitable):
        from .crud import update_swap_status

        try:
            awaited = await awaitable
            await update_swap_status(swap_id, "complete")
            return awaited
        except asyncio.exceptions.CancelledError:
            """lnbits process was exited, do nothing and handle it in startup script"""
        except Exception:
            wstask.cancel()
            await update_swap_status(swap_id, "failed")

    return asyncio.create_task(_pay_invoice(awaitable))
