from datetime import datetime
from typing import List, Optional, Union

from lnbits.db import Database
from lnbits.helpers import insert_query, update_query, urlsafe_short_hash
from loguru import logger

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

db = Database("ext_boltz")


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
    swap_response: BoltzSwapResponse,
    swap_id: str,
    refund_privkey_wif: str,
    payment_hash: str,
) -> SubmarineSwap:

    swap = SubmarineSwap(
        id=swap_id,
        time=datetime.now(),
        refund_privkey=refund_privkey_wif,
        payment_hash=payment_hash,
        status="pending",
        boltz_id=swap_response.id,
        expected_amount=swap_response.expectedAmount,
        timeout_block_height=swap_response.timeoutBlockHeight,
        address=swap_response.address,
        bip21=swap_response.bip21,
        redeem_script=swap_response.redeemScript,
        blinding_key=swap_response.blindingKey,
        **data.dict(),
    )

    await db.execute(
        insert_query("boltz.submarineswap", swap),
        (*swap.dict().values(),),
    )
    new_swap = await get_submarine_swap(swap_id)
    assert new_swap, "Newly created swap not found in database"
    return new_swap


async def get_reverse_submarine_swaps(
    wallet_ids: Union[str, List[str]]
) -> List[ReverseSubmarineSwap]:
    if isinstance(wallet_ids, str):
        wallet_ids = [wallet_ids]

    q = ",".join(["?"] * len(wallet_ids))
    rows = await db.fetchall(
        f"""
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
    swap_id = urlsafe_short_hash()
    reverse_swap = ReverseSubmarineSwap(
        id=swap_id,
        time=datetime.now(),
        claim_privkey=claim_privkey_wif,
        preimage=preimage_hex,
        status="pending",
        boltz_id=swap.id,
        lockup_address=swap.lockupAddress,
        invoice=swap.invoice,
        onchain_amount=swap.onchainAmount,
        timeout_block_height=swap.timeoutBlockHeight,
        redeem_script=swap.redeemScript,
        blinding_key=swap.blindingKey,
        **data.dict(),
    )
    await db.execute(
        insert_query("boltz.reverse_submarineswap", reverse_swap),
        (*reverse_swap.dict().values(),),
    )
    new_swap = await get_reverse_submarine_swap(swap_id)
    assert new_swap, "Newly created swap not found in database"
    return new_swap


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
    create_swap: CreateAutoReverseSubmarineSwap,
) -> AutoReverseSubmarineSwap:
    swap_id = urlsafe_short_hash()
    swap = AutoReverseSubmarineSwap(
        id=swap_id, time=datetime.now(), count=0, **create_swap.dict()
    )
    await db.execute(
        insert_query("boltz.auto_reverse_submarineswap", swap),
        (*swap.dict().values(),),
    )
    new_swap = await get_auto_reverse_submarine_swap(swap_id)
    assert new_swap, "Newly created swap not found in database"
    return new_swap


async def update_auto_swap_count(swap_id: str, count: int):
    await db.execute(
        "UPDATE boltz.auto_reverse_submarineswap SET count = ? WHERE id = ?",
        (
            count,
            swap_id,
        ),
    )


async def delete_auto_reverse_submarine_swap(swap_id):
    await db.execute(
        "DELETE FROM boltz.auto_reverse_submarineswap WHERE id = ?", (swap_id,)
    )


async def update_swap_status(swap_id: str, status: str):

    swap = await get_submarine_swap(swap_id)
    if swap:
        await db.execute(
            "UPDATE boltz.submarineswap SET status = ? WHERE id = ?",
            (
                status,
                swap.id,
            ),
        )
        logger.info(
            f"Boltz - swap status change: {status}. "
            f"boltz_id: {swap.boltz_id}, wallet: {swap.wallet}"
        )
        return swap

    reverse_swap = await get_reverse_submarine_swap(swap_id)
    if reverse_swap:
        await db.execute(
            "UPDATE boltz.reverse_submarineswap SET status = ? WHERE id = ?",
            (
                status,
                reverse_swap.id,
            ),
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
            insert_query("boltz.settings", settings), (*settings.dict().values(),)
        )
        return settings


async def update_boltz_settings(settings: BoltzSettings) -> BoltzSettings:
    await db.execute(
        # 3rd arguments `WHERE clause` is empty for settings
        update_query("boltz.settings", settings, ""),
        (*settings.dict().values(),),
    )
    return settings


async def delete_boltz_settings() -> None:
    await db.execute("DELETE FROM boltz.settings")
