from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from lnbits.core.models import KeyType, Wallet, WalletTypeInfo
from lnbits.decorators import require_admin_key

from .. import views_api
from ..models import AutoReverseSubmarineSwap


@pytest.fixture
def wallet_client():
    wallet = Wallet(
        id="caller-wallet",
        user="caller-user",
        name="Caller",
        adminkey="caller-admin-key",
        inkey="caller-invoice-key",
    )
    app = FastAPI()
    app.include_router(views_api.boltz_api_router, prefix="/boltz")
    app.dependency_overrides[require_admin_key] = lambda: WalletTypeInfo(
        KeyType.admin, wallet
    )
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("path", ["", "/reverse", "/reverse/auto"])
def test_create_requires_matching_wallet(wallet_client, monkeypatch, path):
    lookup = AsyncMock(return_value=None)
    check_balance = AsyncMock(return_value=False)
    validate_address = AsyncMock()
    create_swap = AsyncMock(side_effect=HTTPException(418, "Unexpected swap creation"))
    monkeypatch.setattr(views_api, "get_auto_reverse_submarine_swap_by_wallet", lookup)
    monkeypatch.setattr(views_api, "check_balance", check_balance)
    monkeypatch.setattr(views_api, "api_address_validation", validate_address)
    monkeypatch.setattr(views_api, "create_boltz_client", create_swap)
    monkeypatch.setattr(views_api, "create_auto_reverse_submarine_swap", create_swap)

    response = wallet_client.post(
        f"/boltz/api/v1/swap{path}",
        json={
            "wallet": "other-wallet",
            "amount": 20_000,
            "instant_settlement": True,
            "onchain_address": "unused",
            "refund_address": "unused",
        },
    )

    assert response.status_code == 403
    lookup.assert_not_awaited()
    check_balance.assert_not_awaited()
    validate_address.assert_not_awaited()
    create_swap.assert_not_awaited()


@pytest.mark.parametrize(
    "method,path,lookup_name",
    [
        ("DELETE", "/reverse/auto/swap", "get_auto_reverse_submarine_swap"),
        ("POST", "/refund?swap_id=swap", "get_submarine_swap"),
        ("POST", "/status?swap_id=swap", "get_submarine_swap"),
        ("POST", "/status?swap_id=swap", "get_reverse_submarine_swap"),
    ],
)
@pytest.mark.parametrize("exists", [False, True])
def test_swap_lookup_requires_matching_wallet(
    wallet_client, monkeypatch, method, path, lookup_name, exists
):
    for name in (
        "get_auto_reverse_submarine_swap",
        "get_submarine_swap",
        "get_reverse_submarine_swap",
    ):
        monkeypatch.setattr(
            views_api, name, AsyncMock(return_value=None), raising=False
        )
    swap = SimpleNamespace(wallet="other-wallet", status="pending")
    monkeypatch.setattr(
        views_api, lookup_name, AsyncMock(return_value=swap if exists else None)
    )
    delete_swap = AsyncMock()
    create_client = AsyncMock()
    monkeypatch.setattr(views_api, "delete_auto_reverse_submarine_swap", delete_swap)
    monkeypatch.setattr(views_api, "create_boltz_client", create_client)

    response = wallet_client.request(method, f"/boltz/api/v1/swap{path}")

    assert response.status_code == (403 if exists else 404)
    delete_swap.assert_not_awaited()
    create_client.assert_not_awaited()


def test_auto_swap_create_and_delete_for_matching_wallet(wallet_client, monkeypatch):
    data = {
        "wallet": "caller-wallet",
        "asset": "BTC/BTC",
        "amount": 20_000,
        "balance": 0,
        "instant_settlement": True,
        "onchain_address": "unused",
    }
    swap = AutoReverseSubmarineSwap(id="swap", count=0, **data)
    create_swap = AsyncMock(return_value=swap)
    delete_swap = AsyncMock()
    monkeypatch.setattr(
        views_api,
        "get_auto_reverse_submarine_swap_by_wallet",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(views_api, "api_address_validation", AsyncMock())
    monkeypatch.setattr(views_api, "create_auto_reverse_submarine_swap", create_swap)
    monkeypatch.setattr(
        views_api,
        "get_auto_reverse_submarine_swap",
        AsyncMock(return_value=swap),
        raising=False,
    )
    monkeypatch.setattr(views_api, "delete_auto_reverse_submarine_swap", delete_swap)

    response = wallet_client.post("/boltz/api/v1/swap/reverse/auto", json=data)
    assert response.status_code == 201
    assert response.json()["wallet"] == data["wallet"]
    create_swap.assert_awaited_once()

    response = wallet_client.delete("/boltz/api/v1/swap/reverse/auto/swap")
    assert response.status_code == 200
    delete_swap.assert_awaited_once_with("swap")
