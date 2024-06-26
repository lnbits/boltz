import asyncio
import calendar
import datetime
from typing import Awaitable

from lnbits.core.crud import get_wallet
from lnbits.core.services import fee_reserve_total, pay_invoice

from .boltz_client.boltz import BoltzClient, BoltzConfig
from .crud import get_or_create_boltz_settings
from .models import ReverseSubmarineSwap


async def create_boltz_client(pair: str = "BTC/BTC") -> BoltzClient:
    settings = await get_or_create_boltz_settings()
    config = BoltzConfig(
        referral_id="lnbits",
        api_url=settings.boltz_url,
        network=settings.boltz_network,
        network_liquid=settings.boltz_network_liquid,
    )
    return BoltzClient(config, pair)


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
    date = datetime.datetime.utcnow()
    return calendar.timegm(date.utctimetuple())


async def execute_reverse_swap(client: BoltzClient, swap: ReverseSubmarineSwap):
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
