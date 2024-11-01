from http import HTTPStatus
from importlib import util

from fastapi import APIRouter, Depends, HTTPException, Query
from lnbits.core.crud import get_user
from lnbits.core.models import WalletTypeInfo
from lnbits.core.services import create_invoice
from lnbits.decorators import (
    check_admin,
    require_admin_key,
    require_invoice_key,
)
from lnbits.helpers import urlsafe_short_hash

from .boltz_client.boltz import SwapDirection
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

try:
    util.find_spec("wallycore")
    liquid_support = True
except ImportError:
    liquid_support = False


boltz_api_router = APIRouter()


def api_liquid_support(asset: str):
    if asset == "L-BTC/BTC" and not liquid_support:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=(
                "Optional Liquid support is not installed. "
                "Ask admin to run `poetry install -E liquid` to install it."
            ),
        )


async def api_address_validation(address: str, asset: str):
    settings = await get_or_create_boltz_settings()
    if asset == "L-BTC/BTC":
        net = settings.boltz_network_liquid
    else:
        net = settings.boltz_network
    validate_address(address, net, asset)


# NORMAL SWAP
@boltz_api_router.get(
    "/api/v1/swap",
    name="boltz.get /swap",
    summary="get a list of swaps a swap",
    description="""
        This endpoint gets a list of normal swaps.
    """,
    response_description="list of normal swaps",
    dependencies=[Depends(require_invoice_key)],
    response_model=list[SubmarineSwap],
)
async def api_submarineswap(
    key_info: WalletTypeInfo = Depends(require_invoice_key),
    all_wallets: bool = Query(False),
) -> list[SubmarineSwap]:
    wallet_ids = [key_info.wallet.id]
    if all_wallets:
        user = await get_user(key_info.wallet.user)
        wallet_ids = user.wallet_ids if user else []
    return await get_submarine_swaps(wallet_ids)


@boltz_api_router.post(
    "/api/v1/swap/refund",
    name="boltz.swap_refund",
    summary="refund of a swap",
    description="""
        This endpoint attempts to refund a normal swaps,
        creates an onchain tx and sets swap status to refunded.
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
        client = await create_boltz_client(swap.asset)
        await client.refund_swap(
            boltz_id=swap.boltz_id,
            privkey_wif=swap.refund_privkey,
            lockup_address=swap.address,
            receive_address=swap.refund_address,
            redeem_script_hex=swap.redeem_script,
            timeout_block_height=swap.timeout_block_height,
            # feerate=swap.feerate_value if swap.feerate else None,
            blinding_key=swap.blinding_key,
        )

        await update_swap_status(swap.id, "refunded")
        return swap
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)
        ) from exc


@boltz_api_router.post(
    "/api/v1/swap",
    status_code=HTTPStatus.CREATED,
    name="boltz.post /swap",
    summary="create a submarine swap",
    description="""
        This endpoint creates a submarine swap
    """,
    response_description="create swap",
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
async def api_submarineswap_create(data: CreateSubmarineSwap) -> SubmarineSwap:
    auto_swap = await get_auto_reverse_submarine_swap_by_wallet(data.wallet)
    if auto_swap:
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED,
            detail=(
                "auto reverse swap is active, a swap would "
                "immediatly be swapped out again."
            ),
        )
    await api_address_validation(data.refund_address, data.asset)
    api_liquid_support(data.asset)
    client = await create_boltz_client(data.asset)
    if data.direction == SwapDirection.send:
        amount = client.substract_swap_fees(data.amount)
    elif data.direction == SwapDirection.receive:
        amount = data.amount
    else:
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED,
            detail=f"swap direction: {data.direction} not supported",
        )

    swap_id = urlsafe_short_hash()
    payment = await create_invoice(
        wallet_id=data.wallet,
        amount=amount,
        memo=f"swap of {amount} sats on boltz.exchange",
        extra={"tag": "boltz", "swap_id": swap_id},
        expiry=60 * 60 * 24,  # 1 day
    )
    refund_privkey_wif, swap = client.create_swap(payment.bolt11)

    new_swap = await create_submarine_swap(
        data, swap, swap_id, refund_privkey_wif, payment.payment_hash
    )
    return new_swap


# REVERSE SWAP
@boltz_api_router.get(
    "/api/v1/swap/reverse",
    name="boltz.get /swap/reverse",
    summary="get a list of reverse swaps",
    description="""
        This endpoint gets a list of reverse swaps.
    """,
    response_description="list of reverse swaps",
    dependencies=[Depends(require_invoice_key)],
    response_model=list[ReverseSubmarineSwap],
)
async def api_reverse_submarineswap(
    key_info: WalletTypeInfo = Depends(require_invoice_key),
    all_wallets: bool = Query(False),
):
    wallet_ids = [key_info.wallet.id]
    if all_wallets:
        user = await get_user(key_info.wallet.user)
        wallet_ids = user.wallet_ids if user else []
    return await get_reverse_submarine_swaps(wallet_ids)


@boltz_api_router.post(
    "/api/v1/swap/reverse",
    status_code=HTTPStatus.CREATED,
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

    await api_address_validation(data.onchain_address, data.asset)

    api_liquid_support(data.asset)

    client = await create_boltz_client(data.asset)

    if data.direction == SwapDirection.send:
        amount = data.amount
    elif data.direction == SwapDirection.receive:
        amount = client.add_reverse_swap_fees(data.amount)
    else:
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED,
            detail=f"swap direction: {data.direction} not supported",
        )

    claim_privkey_wif, preimage_hex, swap = client.create_reverse_swap(
        amount=amount,
    )
    new_swap = await create_reverse_submarine_swap(
        data, claim_privkey_wif, preimage_hex, swap
    )
    await execute_reverse_swap(client, new_swap)
    return new_swap


@boltz_api_router.get(
    "/api/v1/swap/reverse/auto",
    name="boltz.get /swap/reverse/auto",
    summary="get a list of auto reverse swaps",
    description="""
        This endpoint gets a list of auto reverse swaps.
    """,
    response_description="list of auto reverse swaps",
    dependencies=[Depends(require_invoice_key)],
    response_model=list[AutoReverseSubmarineSwap],
)
async def api_auto_reverse_submarineswap(
    key_info: WalletTypeInfo = Depends(require_invoice_key),
    all_wallets: bool = Query(False),
) -> list[AutoReverseSubmarineSwap]:
    wallet_ids = [key_info.wallet.id]
    if all_wallets:
        user = await get_user(key_info.wallet.user)
        wallet_ids = user.wallet_ids if user else []
    return await get_auto_reverse_submarine_swaps(wallet_ids)


@boltz_api_router.post(
    "/api/v1/swap/reverse/auto",
    status_code=HTTPStatus.CREATED,
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
async def api_auto_reverse_submarineswap_create(
    data: CreateAutoReverseSubmarineSwap,
) -> AutoReverseSubmarineSwap:
    auto_swap = await get_auto_reverse_submarine_swap_by_wallet(data.wallet)
    if auto_swap:
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED,
            detail="auto reverse swap is active, only 1 swap per wallet possible.",
        )
    await api_address_validation(data.onchain_address, data.asset)
    swap = await create_auto_reverse_submarine_swap(data)
    return swap


@boltz_api_router.delete(
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


@boltz_api_router.post(
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
        ) from exc


@boltz_api_router.get(
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
        return client.pairs
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.METHOD_NOT_ALLOWED, detail=str(exc)
        ) from exc


@boltz_api_router.get("/api/v1/settings", dependencies=[Depends(check_admin)])
async def api_get_or_create_settings() -> BoltzSettings:
    return await get_or_create_boltz_settings()


@boltz_api_router.put("/api/v1/settings", dependencies=[Depends(check_admin)])
async def api_update_settings(data: BoltzSettings) -> BoltzSettings:
    return await update_boltz_settings(data)


@boltz_api_router.delete("/api/v1/settings", dependencies=[Depends(check_admin)])
async def api_delete_settings() -> None:
    await delete_boltz_settings()
