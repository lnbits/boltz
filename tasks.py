import asyncio

from lnbits.core.crud import get_wallet
from lnbits.core.models import Payment
from lnbits.core.services import check_transaction_status, fee_reserve_total
from lnbits.tasks import register_invoice_listener
from loguru import logger

from .boltz_client.boltz import BoltzNotFoundException, BoltzSwapStatusException
from .boltz_client.mempool import MempoolBlockHeightException
from .crud import (
    create_reverse_submarine_swap,
    get_all_pending_reverse_submarine_swaps,
    get_all_pending_submarine_swaps,
    get_auto_reverse_submarine_swap_by_wallet,
    get_submarine_swap,
    update_auto_swap_count,
    update_swap_status,
)
from .models import CreateReverseSubmarineSwap, ReverseSubmarineSwap, SubmarineSwap
from .utils import create_boltz_client, execute_reverse_swap


async def wait_for_paid_invoices():
    invoice_queue = asyncio.Queue()
    register_invoice_listener(invoice_queue, "ext_boltz")

    while True:
        payment = await invoice_queue.get()
        await on_invoice_paid(payment)


async def on_invoice_paid(payment: Payment) -> None:

    await check_for_auto_swap(payment)

    if payment.extra.get("tag") != "boltz":
        # not a boltz invoice
        return

    swap_id = payment.extra.get("swap_id")
    if swap_id:
        swap = await get_submarine_swap(swap_id)
        if swap:
            await update_swap_status(swap_id, "complete")


async def check_for_auto_swap(payment: Payment) -> None:
    auto_swap = await get_auto_reverse_submarine_swap_by_wallet(payment.wallet_id)
    if auto_swap:
        wallet = await get_wallet(payment.wallet_id)
        if wallet:
            reserve = fee_reserve_total(wallet.balance_msat) / 1000
            balance = wallet.balance_msat / 1000
            amount = balance - auto_swap.balance - reserve
            if amount >= auto_swap.amount:
                try:
                    client = await create_boltz_client(auto_swap.asset)
                except Exception as exc:
                    logger.error(f"Boltz API issues: {exc!s}")
                    return
                fees = client.get_fee_estimation_claim()
                if auto_swap.feerate_limit and fees > auto_swap.feerate_limit:
                    logger.warning(
                        "Boltz: auto reverse swap not created, fee limit exceeded: "
                        f"{auto_swap.feerate_limit}, actual fees: {fees}"
                    )
                    return
                claim_privkey_wif, preimage_hex, swap = client.create_reverse_swap(
                    amount=int(amount)
                )
                new_swap = await create_reverse_submarine_swap(
                    CreateReverseSubmarineSwap(
                        wallet=auto_swap.wallet,
                        amount=int(amount),
                        instant_settlement=auto_swap.instant_settlement,
                        onchain_address=auto_swap.onchain_address,
                        feerate=False,
                    ),
                    claim_privkey_wif,
                    preimage_hex,
                    swap,
                )
                await execute_reverse_swap(client, new_swap)
                await update_auto_swap_count(auto_swap.id, auto_swap.count + 1)

                logger.info(
                    "Boltz: auto reverse swap created with amount: "
                    f"{amount}, boltz_id: {new_swap.boltz_id}"
                )


async def check_for_pending_swaps():
    try:
        swaps = await get_all_pending_submarine_swaps()
        reverse_swaps = await get_all_pending_reverse_submarine_swaps()
        if len(swaps) > 0 or len(reverse_swaps) > 0:
            logger.debug("Boltz - startup swap check")
    except Exception:
        logger.error(
            "Boltz - startup swap check, database is not created yet, do nothing"
        )
        return

    if len(swaps) > 0:
        logger.debug(f"Boltz - {len(swaps)} pending swaps")
        for swap in swaps:
            await check_swap(swap)

    if len(reverse_swaps) > 0:
        logger.debug(f"Boltz - {len(reverse_swaps)} pending reverse swaps")
        for reverse_swap in reverse_swaps:
            await check_reverse_swap(reverse_swap)


async def check_swap(swap: SubmarineSwap):
    try:
        payment_status = await check_transaction_status(swap.wallet, swap.payment_hash)
        if payment_status.paid:
            logger.debug(f"Boltz - swap: {swap.boltz_id} got paid while offline.")
            await update_swap_status(swap.id, "complete")
        else:
            client = await create_boltz_client(swap.asset)
            try:
                _ = client.swap_status(swap.id)
            except Exception:
                await client.refund_swap(
                    privkey_wif=swap.refund_privkey,
                    boltz_id=swap.boltz_id,
                    lockup_address=swap.address,
                    receive_address=swap.refund_address,
                    redeem_script_hex=swap.redeem_script,
                    timeout_block_height=swap.timeout_block_height,
                    # feerate=swap.feerate_value if swap.feerate else None,
                    blinding_key=swap.blinding_key,
                )
                await update_swap_status(swap.id, "refunded")
    except BoltzNotFoundException:
        logger.debug(f"Boltz - swap: {swap.boltz_id} does not exist.")
        await update_swap_status(swap.id, "failed")
    except MempoolBlockHeightException:
        logger.debug(
            f"Boltz - tried to refund swap: {swap.id}, but has not reached the timeout."
        )
    except Exception as exc:
        logger.error(f"Boltz - unhandled exception, swap: {swap.id} - {exc!s}")


async def check_reverse_swap(reverse_swap: ReverseSubmarineSwap):
    try:

        client = await create_boltz_client(reverse_swap.asset)
        _ = client.swap_status(reverse_swap.boltz_id)
        await client.claim_reverse_swap(
            boltz_id=reverse_swap.boltz_id,
            lockup_address=reverse_swap.lockup_address,
            receive_address=reverse_swap.onchain_address,
            privkey_wif=reverse_swap.claim_privkey,
            preimage_hex=reverse_swap.preimage,
            redeem_script_hex=reverse_swap.redeem_script,
            zeroconf=reverse_swap.instant_settlement,
            # feerate=reverse_swap.feerate_value if reverse_swap.feerate else None,
            blinding_key=reverse_swap.blinding_key,
        )
        await update_swap_status(reverse_swap.id, "complete")

    except BoltzSwapStatusException as exc:
        logger.debug(f"Boltz - swap_status: {exc!s}")
        await update_swap_status(reverse_swap.id, "failed")
    # should only happen while development when regtest is reset
    except BoltzNotFoundException:
        logger.debug(f"Boltz - reverse swap: {reverse_swap.boltz_id} does not exist.")
        await update_swap_status(reverse_swap.id, "failed")
    except Exception as exc:
        logger.error(
            "Boltz - unhandled exception, reverse swap: "
            f"{reverse_swap.boltz_id} - {exc!s}"
        )
