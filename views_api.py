from http import HTTPStatus
from typing import List

from fastapi import Depends, Query, status
from fastapi.exceptions import HTTPException
from loguru import logger

from lnbits.core.crud import get_user
from lnbits.core.services import create_invoice
from lnbits.decorators import (
    WalletTypeInfo,
    check_admin,
    get_key_type,
    require_admin_key,
)
from lnbits.helpers import urlsafe_short_hash

from . import boltz_ext, scheduled_tasks
from .boltz_client.onchain import validate_address
from .crud import (
    create_auto_reverse_submarine_swap,
    create_reverse_submarine_swap,
    create_submarine_swap,
    delete_auto_reverse_submarine_swap,
    delete_boltz_settings,
    get_auto_reverse_submarine_swap_by_wallet,
    get_auto_reverse_submarine_swaps,
    get_or_create_boltz_settings,
    get_reverse_submarine_swap,
    get_reverse_submarine_swaps,
    get_submarine_swap,
    get_submarine_swaps,
    update_boltz_settings,
    update_swap_status,
)
from .models import (
    AutoReverseSubmarineSwap,
    BoltzSettings,
    CreateAutoReverseSubmarineSwap,
    CreateReverseSubmarineSwap,
    CreateSubmarineSwap,
    ReverseSubmarineSwap,
    SubmarineSwap,
)
from .utils import check_balance, create_boltz_client, execute_reverse_swap


@boltz_ext.get(
    "/api/v1/swap/mempool",
    name="boltz.get /swap/mempool",
    summary="get a the mempool url",
    description="""
        This endpoint gets the URL from mempool.space
    """,
    response_description="mempool.space url",
    response_model=str,
)
async def api_mempool_url():
    settings = await get_or_create_boltz_settings()
    return settings.boltz_mempool_space_url


# NORMAL SWAP
@boltz_ext.get(
    "/api/v1/swap",
    name="boltz.get /swap",
    summary="get a list of swaps a swap",
    description="""
        This endpoint gets a list of normal swaps.
    """,
    response_description="list of normal swaps",
    dependencies=[Depends(get_key_type)],
    response_model=List[SubmarineSwap],
)
async def api_submarineswap(
    g: WalletTypeInfo = Depends(get_key_type),
    all_wallets: bool = Query(False),
):
    wallet_ids = [g.wallet.id]
    if all_wallets:
        user = await get_user(g.wallet.user)
        wallet_ids = user.wallet_ids if user else []
    return [swap.dict() for swap in await get_submarine_swaps(wallet_ids)]


@boltz_ext.post(
    "/api/v1/swap/refund",
    name="boltz.swap_refund",
    summary="refund of a swap",
    description="""
        This endpoint attempts to refund a normal swaps,
        creates onchain tx and sets swap status ro refunded.
    """,
    response_description="refunded swap with status set to refunded",
    dependencies=[Depends(require_admin_key)],
    response_model=SubmarineSwap,
    responses={
        400: {"description": "when swap_id is missing"},
        404: {"description": "when swap is not found"},
        405: {"description": "when swap is not pending"},
        500: {
            "description": "when something goes wrong creating the refund onchain tx"
        },
    },
)
async def api_submarineswap_refund(swap_id: str):
    if not swap_id:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="swap_id missing"
        )
    swap = await get_submarine_swap(swap_id)
    if not swap:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="swap does not exist."
        )
    if swap.status != "pending":
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED, detail="swap is not pending."
        )

    try:
        client = await create_boltz_client()
        await client.refund_swap(
            boltz_id=swap.boltz_id,
            privkey_wif=swap.refund_privkey,
            lockup_address=swap.address,
            receive_address=swap.refund_address,
            redeem_script_hex=swap.redeem_script,
            timeout_block_height=swap.timeout_block_height,
            feerate=swap.feerate_value if swap.feerate else None,
        )

        await update_swap_status(swap.id, "refunded")
        return swap
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)
        )


@boltz_ext.post(
    "/api/v1/swap",
    status_code=status.HTTP_201_CREATED,
    name="boltz.post /swap",
    summary="create a submarine swap",
    description="""
        This endpoint creates a submarine swap
    """,
    response_description="create swap",
    response_model=SubmarineSwap,
    dependencies=[Depends(require_admin_key)],
    responses={
        405: {
            "description": (
                "auto reverse swap is active, a swap would "
                "immediatly be swapped out again."
            )
        },
        500: {"description": "boltz error"},
    },
)
async def api_submarineswap_create(data: CreateSubmarineSwap):

    auto_swap = await get_auto_reverse_submarine_swap_by_wallet(data.wallet)
    if auto_swap:
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED,
            detail=(
                "auto reverse swap is active, a swap would "
                "immediatly be swapped out again."
            ),
        )

    settings = await get_or_create_boltz_settings()
    try:
        validate_address(data.refund_address, settings.boltz_network)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED,
            detail=f"Refund Address: {str(exc)}"
        )

    try:
        client = await create_boltz_client()

        if data.direction == "send":
            amount = client.substract_swap_fees(data.amount)
        elif data.direction == "receive":
            amount = data.amount
        else:
            raise HTTPException(
                status_code=HTTPStatus.METHOD_NOT_ALLOWED,
                detail=f"swap direction: {data.direction} not supported"
            )

        swap_id = urlsafe_short_hash()
        payment_hash, payment_request = await create_invoice(
            wallet_id=data.wallet,
            amount=amount,
            memo=f"swap of {amount} sats on boltz.exchange",
            extra={"tag": "boltz", "swap_id": swap_id},
            expiry=60*60*24, # 1 day
        )
        refund_privkey_wif, swap = client.create_swap(payment_request)
        new_swap = await create_submarine_swap(
            data, swap, swap_id, refund_privkey_wif, payment_hash
        )
        return new_swap.dict() if new_swap else None
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED, detail=str(exc)
        )


# REVERSE SWAP
@boltz_ext.get(
    "/api/v1/swap/reverse",
    name="boltz.get /swap/reverse",
    summary="get a list of reverse swaps",
    description="""
        This endpoint gets a list of reverse swaps.
    """,
    response_description="list of reverse swaps",
    dependencies=[Depends(get_key_type)],
    response_model=List[ReverseSubmarineSwap],
)
async def api_reverse_submarineswap(
    g: WalletTypeInfo = Depends(get_key_type),
    all_wallets: bool = Query(False),
):
    wallet_ids = [g.wallet.id]
    if all_wallets:
        user = await get_user(g.wallet.user)
        wallet_ids = user.wallet_ids if user else []
    return [swap for swap in await get_reverse_submarine_swaps(wallet_ids)]


@boltz_ext.post(
    "/api/v1/swap/reverse",
    status_code=status.HTTP_201_CREATED,
    name="boltz.post /swap/reverse",
    summary="create a reverse submarine swap",
    description="""
        This endpoint creates a reverse submarine swap
    """,
    response_description="create reverse swap",
    response_model=ReverseSubmarineSwap,
    dependencies=[Depends(require_admin_key)],
    responses={
        405: {"description": "not allowed method, insufficient balance"},
        500: {"description": "boltz error"},
    },
)
async def api_reverse_submarineswap_create(
    data: CreateReverseSubmarineSwap,
) -> ReverseSubmarineSwap:

    if not await check_balance(data):
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED, detail="Insufficient balance."
        )
    settings = await get_or_create_boltz_settings()
    try:
        validate_address(data.onchain_address, settings.boltz_network)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED,
            detail=f"Onchain Address: {str(exc)}"
        )
    try:
        client = await create_boltz_client()

        if data.direction == "send":
            amount = data.amount
        elif data.direction == "receive":
            amount = client.add_reverse_swap_fees(data.amount)
        else:
            raise HTTPException(
                status_code=HTTPStatus.METHOD_NOT_ALLOWED,
                detail=f"swap direction: {data.direction} not supported"
            )

        claim_privkey_wif, preimage_hex, swap = client.create_reverse_swap(
            amount=amount,
        )
        new_swap = await create_reverse_submarine_swap(
            data, claim_privkey_wif, preimage_hex, swap
        )
        await execute_reverse_swap(client, new_swap)
        return new_swap
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED, detail=str(exc)
        )


@boltz_ext.get(
    "/api/v1/swap/reverse/auto",
    name="boltz.get /swap/reverse/auto",
    summary="get a list of auto reverse swaps",
    description="""
        This endpoint gets a list of auto reverse swaps.
    """,
    response_description="list of auto reverse swaps",
    dependencies=[Depends(get_key_type)],
    response_model=List[AutoReverseSubmarineSwap],
)
async def api_auto_reverse_submarineswap(
    g: WalletTypeInfo = Depends(get_key_type),
    all_wallets: bool = Query(False),
):
    wallet_ids = [g.wallet.id]
    if all_wallets:
        user = await get_user(g.wallet.user)
        wallet_ids = user.wallet_ids if user else []
    return [swap.dict() for swap in await get_auto_reverse_submarine_swaps(wallet_ids)]


@boltz_ext.post(
    "/api/v1/swap/reverse/auto",
    status_code=status.HTTP_201_CREATED,
    name="boltz.post /swap/reverse/auto",
    summary="create a auto reverse submarine swap",
    description="""
        This endpoint creates a auto reverse submarine swap
    """,
    response_description="create auto reverse swap",
    response_model=AutoReverseSubmarineSwap,
    dependencies=[Depends(require_admin_key)],
    responses={
        405: {
            "description": (
                "auto reverse swap is active, only 1 swap per wallet possible."
            )
        },
    },
)
async def api_auto_reverse_submarineswap_create(data: CreateAutoReverseSubmarineSwap):

    auto_swap = await get_auto_reverse_submarine_swap_by_wallet(data.wallet)
    if auto_swap:
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED,
            detail="auto reverse swap is active, only 1 swap per wallet possible.",
        )

    settings = await get_or_create_boltz_settings()
    try:
        validate_address(data.onchain_address, settings.boltz_network)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED,
            detail=f"Onchain Address: {str(exc)}"
        )

    swap = await create_auto_reverse_submarine_swap(data)
    return swap.dict() if swap else None


@boltz_ext.delete(
    "/api/v1/swap/reverse/auto/{swap_id}",
    name="boltz.delete /swap/reverse/auto",
    summary="delete a auto reverse submarine swap",
    description="""
        This endpoint deletes a auto reverse submarine swap
    """,
    response_description="delete auto reverse swap",
    dependencies=[Depends(require_admin_key)],
)
async def api_auto_reverse_submarineswap_delete(swap_id: str):
    await delete_auto_reverse_submarine_swap(swap_id)
    return "OK"


@boltz_ext.post(
    "/api/v1/swap/status",
    name="boltz.swap_status",
    summary="shows the status of a swap",
    description="""
        This endpoint attempts to get the status of the swap.
    """,
    response_description="status of swap json",
    dependencies=[Depends(require_admin_key)],
    responses={
        404: {"description": "when swap_id is not found"},
    },
)
async def api_swap_status(swap_id: str):
    swap = await get_submarine_swap(swap_id) or await get_reverse_submarine_swap(
        swap_id
    )
    if not swap:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="swap does not exist."
        )
    try:
        client = await create_boltz_client()
        status = client.swap_status(swap.boltz_id)
        return status
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED, detail=str(exc)
        )


@boltz_ext.get(
    "/api/v1/swap/boltz",
    name="boltz.get /swap/boltz",
    summary="get a boltz configuration",
    description="""
        This endpoint gets configuration for boltz. (limits, fees...)
    """,
    response_description="dict of boltz config",
    response_model=dict,
)
async def api_boltz_config():
    try:
        client = await create_boltz_client()
        return {
            "minimal": client.limit_minimal,
            "maximal": client.limit_maximal,
            "fee_percentage": client.fees["percentage"],
        }
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED, detail=str(exc)
        )


@boltz_ext.delete(
    "/api/v1",
    status_code=HTTPStatus.OK,
    dependencies=[Depends(check_admin)]
)
async def api_stop():
    for t in scheduled_tasks:
        try:
            t.cancel()
        except Exception as ex:
            logger.warning(ex)

    return {"success": True}


@boltz_ext.get("/api/v1/settings", dependencies=[Depends(check_admin)])
async def api_get_or_create_settings() -> BoltzSettings:
    return await get_or_create_boltz_settings()


@boltz_ext.put("/api/v1/settings", dependencies=[Depends(check_admin)])
async def api_update_settings(data: BoltzSettings) -> BoltzSettings:
    return await update_boltz_settings(data)


@boltz_ext.delete("/api/v1/settings", dependencies=[Depends(check_admin)])
async def api_delete_settings() -> None:
    await delete_boltz_settings()
