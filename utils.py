import asyncio
import calendar
import datetime
from collections.abc import Awaitable

from lnbits.core.crud import get_wallet
from lnbits.core.services import fee_reserve_total, pay_invoice
from loguru import logger

from .boltz_client.boltz import BoltzClient, BoltzConfig
from .boltz_client.boltz_native import boltz_client_available
from .crud import get_or_create_boltz_settings
from .models import ReverseSubmarineSwap


async def create_boltz_client(pair: str = "BTC/BTC") -> BoltzClient:
    settings = await get_or_create_boltz_settings()
    if not boltz_client_available():
        raise RuntimeError(
            "Boltz transaction support is not installed. "
            "Install LNbits with the `liquid` extra or `--all-extras`."
        )
    pairs = ["BTC/BTC"]
    pairs.append("L-BTC/BTC")
    config = BoltzConfig(
        pairs=pairs,
        referral_id="lnbits",
        api_url=settings.boltz_url,
        network=settings.boltz_network,
        network_liquid=settings.boltz_network_liquid,
        liquid_esplora_url=settings.boltz_liquid_esplora_url,
    )
    client = BoltzClient(config, pair)
    await client.init_pairs()
    return client


async def check_balance(data, amount: int | None = None) -> bool:
    # check if we can pay the invoice before we create the actual swap on boltz
    amount_msat = (amount or data.amount) * 1000
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
            invoice=swap.invoice,
            receive_address=swap.onchain_address,
            redeem_script_hex=swap.redeem_script,
            zeroconf=swap.instant_settlement,
            # feerate=swap.feerate_value if swap.feerate else None,
            blinding_key=swap.blinding_key,
            timeout_block_height=swap.timeout_block_height,
            onchain_amount=swap.onchain_amount,
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
    asyncio.create_task(watch_reverse_swap_tasks(swap.id, claim_task, pay_task))


async def watch_reverse_swap_tasks(
    swap_id: str, claim_task: asyncio.Task, pay_task: asyncio.Task
) -> None:
    from .crud import update_swap_status

    try:
        await asyncio.gather(claim_task, pay_task)
        await update_swap_status(swap_id, "complete")
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.exception(f"Boltz - reverse swap task failed, swap: {swap_id} - {exc!s}")
        if not claim_task.cancelled():
            claim_task.cancel()
        if not pay_task.cancelled():
            pay_task.cancel()
        await update_swap_status(swap_id, "failed")


def pay_invoice_and_update_status(
    swap_id: str, wstask: asyncio.Task, awaitable: Awaitable
) -> asyncio.Task:
    async def _pay_invoice(awaitable):
        from .crud import update_swap_status

        try:
            return await awaitable
        except asyncio.exceptions.CancelledError:
            """lnbits process was exited, do nothing and handle it in startup script"""
        except Exception as exc:
            logger.error(f"Boltz - reverse swap payment failed: {swap_id} - {exc!s}")
            wstask.cancel()
            await update_swap_status(swap_id, "failed")

    return asyncio.create_task(_pay_invoice(awaitable))
