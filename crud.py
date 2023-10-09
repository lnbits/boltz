import time
from typing import List, Optional, Union

from loguru import logger

from lnbits.helpers import insert_query, update_query, urlsafe_short_hash

from . import db
from .boltz_client.boltz import BoltzReverseSwapResponse, BoltzSwapResponse
from .models import (
    AutoReverseSubmarineSwap,
    BoltzSettings,
    CreateAutoReverseSubmarineSwap,
    CreateReverseSubmarineSwap,
    CreateSubmarineSwap,
    ReverseSubmarineSwap,
    SubmarineSwap,
)


async def get_submarine_swaps(wallet_ids: Union[str, List[str]]) -> List[SubmarineSwap]:
    if isinstance(wallet_ids, str):
        wallet_ids = [wallet_ids]

    q = ",".join(["?"] * len(wallet_ids))
    rows = await db.fetchall(
        f"SELECT * FROM boltz.submarineswap WHERE wallet IN ({q}) order by time DESC",
        (*wallet_ids,),
    )

    return [SubmarineSwap(**row) for row in rows]


async def get_all_pending_submarine_swaps() -> List[SubmarineSwap]:
    rows = await db.fetchall(
        "SELECT * FROM boltz.submarineswap WHERE status='pending' order by time DESC",
    )
    return [SubmarineSwap(**row) for row in rows]


async def get_submarine_swap(swap_id) -> Optional[SubmarineSwap]:
    row = await db.fetchone(
        "SELECT * FROM boltz.submarineswap WHERE id = ?", (swap_id,)
    )
    return SubmarineSwap(**row) if row else None


async def create_submarine_swap(
    data: CreateSubmarineSwap,
    swap: BoltzSwapResponse,
    swap_id: str,
    refund_privkey_wif: str,
    payment_hash: str,
) -> Optional[SubmarineSwap]:

    await db.execute(
        """
        INSERT INTO boltz.submarineswap (
            id,
            wallet,
            payment_hash,
            status,
            boltz_id,
            refund_privkey,
            refund_address,
            expected_amount,
            timeout_block_height,
            address,
            bip21,
            redeem_script,
            amount,
            feerate,
            feerate_value
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            swap_id,
            data.wallet,
            payment_hash,
            "pending",
            swap.id,
            refund_privkey_wif,
            data.refund_address,
            swap.expectedAmount,
            swap.timeoutBlockHeight,
            swap.address,
            swap.bip21,
            swap.redeemScript,
            data.amount,
            data.feerate,
            data.feerate_value,
        ),
    )
    return await get_submarine_swap(swap_id)


async def get_reverse_submarine_swaps(
    wallet_ids: Union[str, List[str]]
) -> List[ReverseSubmarineSwap]:
    if isinstance(wallet_ids, str):
        wallet_ids = [wallet_ids]

    q = ",".join(["?"] * len(wallet_ids))
    rows = await db.fetchall(f"""
            SELECT * FROM boltz.reverse_submarineswap
            WHERE wallet IN ({q}) order by time DESC
        """,
        (*wallet_ids,),
    )

    return [ReverseSubmarineSwap(**row) for row in rows]


async def get_all_pending_reverse_submarine_swaps() -> List[ReverseSubmarineSwap]:
    rows = await db.fetchall(
        "SELECT * FROM boltz.reverse_submarineswap "
        "WHERE status='pending' order by time DESC"
    )

    return [ReverseSubmarineSwap(**row) for row in rows]


async def get_reverse_submarine_swap(swap_id) -> Optional[ReverseSubmarineSwap]:
    row = await db.fetchone(
        "SELECT * FROM boltz.reverse_submarineswap WHERE id = ?", (swap_id,)
    )
    return ReverseSubmarineSwap(**row) if row else None


async def create_reverse_submarine_swap(
    data: CreateReverseSubmarineSwap,
    claim_privkey_wif: str,
    preimage_hex: str,
    swap: BoltzReverseSwapResponse,
) -> ReverseSubmarineSwap:
    reverse_swap = ReverseSubmarineSwap(
        id=urlsafe_short_hash(),
        time=int(time.time()),
        claim_privkey=claim_privkey_wif,
        preimage=preimage_hex,
        status="pending",

        wallet=data.wallet,
        instant_settlement=data.instant_settlement,
        onchain_address=data.onchain_address,
        amount=data.amount,
        feerate=data.feerate,
        feerate_value=data.feerate_value,

        boltz_id=swap.id,
        lockup_address=swap.lockupAddress,
        invoice=swap.invoice,
        onchain_amount=swap.onchainAmount,
        timeout_block_height=swap.timeoutBlockHeight,
        redeem_script=swap.redeemScript,
    )
    await db.execute(
        insert_query("boltz.reverse_submarineswap", reverse_swap),
        (*reverse_swap.dict().items(),),
    )
    return reverse_swap


async def get_auto_reverse_submarine_swaps(
    wallet_ids: List[str],
) -> List[AutoReverseSubmarineSwap]:
    q = ",".join(["?"] * len(wallet_ids))
    rows = await db.fetchall(
        f"""
            SELECT * FROM boltz.auto_reverse_submarineswap
            WHERE wallet IN ({q}) order by time DESC
        """,
        (*wallet_ids,),
    )
    return [AutoReverseSubmarineSwap(**row) for row in rows]


async def get_auto_reverse_submarine_swap(
    swap_id,
) -> Optional[AutoReverseSubmarineSwap]:
    row = await db.fetchone(
        "SELECT * FROM boltz.auto_reverse_submarineswap WHERE id = ?", (swap_id,)
    )
    return AutoReverseSubmarineSwap(**row) if row else None


async def get_auto_reverse_submarine_swap_by_wallet(
    wallet_id,
) -> Optional[AutoReverseSubmarineSwap]:
    row = await db.fetchone(
        "SELECT * FROM boltz.auto_reverse_submarineswap WHERE wallet = ?", (wallet_id,)
    )
    return AutoReverseSubmarineSwap(**row) if row else None


async def create_auto_reverse_submarine_swap(
    new_swap: CreateAutoReverseSubmarineSwap,
) -> AutoReverseSubmarineSwap:
    swap = AutoReverseSubmarineSwap(
        id=urlsafe_short_hash(),
        **new_swap.dict()
    )
    await db.execute(
        insert_query("boltz.auto_reverse_submarineswap", swap),
        (*swap.dict().items(),),
    )
    return swap


async def delete_auto_reverse_submarine_swap(swap_id):
    await db.execute(
        "DELETE FROM boltz.auto_reverse_submarineswap WHERE id = ?", (swap_id,)
    )


async def update_swap_status(swap_id: str, status: str):

    swap = await get_submarine_swap(swap_id)
    if swap:
        await db.execute(
            "UPDATE boltz.submarineswap SET status='"
            + status
            + "' WHERE id='"
            + swap.id
            + "'"
        )
        logger.info(
            f"Boltz - swap status change: {status}. "
            f"boltz_id: {swap.boltz_id}, wallet: {swap.wallet}"
        )
        return swap

    reverse_swap = await get_reverse_submarine_swap(swap_id)
    if reverse_swap:
        await db.execute(
            "UPDATE boltz.reverse_submarineswap SET status='"
            + status
            + "' WHERE id='"
            + reverse_swap.id
            + "'"
        )
        logger.info(
            f"Boltz - reverse swap status change: {status}. "
            f"boltz_id: {reverse_swap.boltz_id}, wallet: {reverse_swap.wallet}"
        )
        return reverse_swap

    return None


async def get_or_create_boltz_settings() -> BoltzSettings:
    row = await db.fetchone("SELECT * FROM boltz.settings LIMIT 1")
    if row:
        return BoltzSettings(**row)
    else:
        settings = BoltzSettings()
        await db.execute(
            insert_query("boltz.settings", settings),
            (*settings.dict().values(),)
        )
        return settings


async def update_boltz_settings(settings: BoltzSettings) -> BoltzSettings:
    await db.execute(
        # 3rd arguments `WHERE clause` is empty for settings
        update_query("boltz.settings", settings, ""),
        (*settings.dict().values(),)
    )
    return settings


async def delete_boltz_settings() -> None:
    await db.execute("DELETE FROM boltz.settings")
