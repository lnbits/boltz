import pytest

from ..boltz_client import boltz
from ..boltz_client.boltz import BoltzClient, BoltzConfig
from ..boltz_client.boltz_native import boltz_client_available
from ..boltz_client.boltz_native import _transaction_fee
from ..boltz_client.onchain_taproot import (
    TaprootSwapData,
    is_taproot_swap_data,
    taproot_swap_data_from_response,
)
from .. import utils
from ..utils import check_balance


@pytest.fixture
def client():
    return BoltzClient(
        BoltzConfig(
            pairs=["BTC/BTC", "L-BTC/BTC"],
            api_url="https://boltz.exchange/api",
        )
    )


@pytest.fixture
def v2_pairs():
    submarine_pair = {
        "hash": "submarine-hash",
        "rate": 1,
        "limits": {"minimal": 1000, "maximal": 1_000_000, "maximalZeroConf": 0},
        "fees": {"percentage": 0.1, "minerFees": 100},
    }
    reverse_pair = {
        "hash": "reverse-hash",
        "rate": 1,
        "limits": {"minimal": 1000, "maximal": 1_000_000},
        "fees": {"percentage": 0.5, "minerFees": {"lockup": 20, "claim": 30}},
    }
    return submarine_pair, reverse_pair


def test_taproot_swap_data_roundtrip():
    swap_tree = {
        "claimLeaf": {"version": 192, "output": "51"},
        "refundLeaf": {"version": 192, "output": "52"},
    }
    server_public_key = "02" + "11" * 32

    raw = taproot_swap_data_from_response(swap_tree, server_public_key)
    parsed = TaprootSwapData.from_json(raw)

    assert is_taproot_swap_data(raw)
    assert parsed.swap_tree == swap_tree
    assert parsed.server_public_key == server_public_key


def test_boltz_client_detection_requires_pypi_boltz_client(monkeypatch):
    import builtins
    import types

    from ..boltz_client import boltz_native

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "boltz_client":
            return types.SimpleNamespace()
        return real_import(name, *args, **kwargs)

    boltz_native._BOLTZ_CLIENT_MODULE = None
    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(boltz_native.PathFinder, "find_spec", lambda *args: None)

    assert not boltz_client_available()


def test_liquid_transaction_fee_has_relay_safe_floor():
    assert _transaction_fee("L-BTC/BTC", 20) == 100
    assert _transaction_fee("L-BTC/BTC", 120) == 120
    assert _transaction_fee("BTC/BTC", 20) == 20


@pytest.mark.asyncio
async def test_liquid_claim_does_not_validate_boltz_lockup_address(monkeypatch):
    client = BoltzClient(
        BoltzConfig(
            pairs=["BTC/BTC", "L-BTC/BTC"],
            api_url="https://boltz.exchange/api",
        ),
        "L-BTC/BTC",
    )
    validated_addresses = []

    def fake_validate_address(address, network, pair):
        validated_addresses.append(address)
        return address

    async def stop_before_network(*args, **kwargs):
        raise RuntimeError("stop")

    monkeypatch.setattr(boltz, "validate_address", fake_validate_address)
    monkeypatch.setattr(client, "wait_for_tx_on_status", stop_before_network)

    with pytest.raises(RuntimeError, match="stop"):
        await client.claim_reverse_swap(
            boltz_id="boltz-id",
            lockup_address="lq1pboltzliquidtaprootlockup",
            invoice="lnbc1invoice",
            receive_address="lq1quserliquidaddress",
            privkey_wif="wif",
            preimage_hex="00" * 32,
            redeem_script_hex=taproot_swap_data_from_response(
                {
                    "claimLeaf": {"version": 192, "output": "51"},
                    "refundLeaf": {"version": 192, "output": "52"},
                },
                "02" + "11" * 32,
            ),
        )

    assert validated_addresses == ["lq1quserliquidaddress"]


@pytest.mark.asyncio
async def test_check_balance_uses_final_reverse_invoice_amount(monkeypatch):
    class Wallet:
        balance_msat = 101_500_000

    class Data:
        wallet = "wallet-id"
        amount = 100_000

    async def fake_get_wallet(wallet_id):
        assert wallet_id == "wallet-id"
        return Wallet()

    monkeypatch.setattr(utils, "get_wallet", fake_get_wallet)
    monkeypatch.setattr(utils, "fee_reserve_total", lambda amount_msat: 1_500_000)

    assert await check_balance(Data())
    assert not await check_balance(Data(), amount=101_000)


@pytest.mark.asyncio
async def test_get_pairs_maps_v2_pairs_to_existing_shape(client, v2_pairs):
    submarine_pair, reverse_pair = v2_pairs

    async def fake_request(method, url, **kwargs):
        assert method == "get"
        if url.endswith("/swap/submarine"):
            return {"BTC": {"BTC": submarine_pair}}
        if url.endswith("/swap/reverse"):
            return {"BTC": {"BTC": reverse_pair}}
        raise AssertionError(f"unexpected url: {url}")

    client.request = fake_request

    pairs = await client.get_pairs()

    assert client._cfg.api_url == "https://api.boltz.exchange/v2"
    assert pairs["BTC/BTC"]["limits"] == submarine_pair["limits"]
    assert pairs["BTC/BTC"]["fees"]["percentage"] == 0.5
    assert pairs["BTC/BTC"]["fees"]["percentageSwapIn"] == 0.1
    assert pairs["BTC/BTC"]["fees"]["minerFees"]["baseAsset"]["normal"] == 100
    assert pairs["BTC/BTC"]["fees"]["minerFees"]["baseAsset"]["reverse"] == {
        "lockup": 20,
        "claim": 30,
    }


@pytest.mark.asyncio
async def test_create_swap_uses_v2_submarine_endpoint(client, v2_pairs):
    submarine_pair, reverse_pair = v2_pairs
    client.pairs = {
        "BTC/BTC": {
            "submarine": submarine_pair,
            "reverse": reverse_pair,
        }
    }

    async def fake_request(method, url, **kwargs):
        assert method == "post"
        assert url == "https://api.boltz.exchange/v2/swap/submarine"
        payload = kwargs["json"]
        assert payload["from"] == "BTC"
        assert payload["to"] == "BTC"
        assert payload["invoice"] == "lnbc1invoice"
        assert payload["pairHash"] == "submarine-hash"
        assert "refundPublicKey" in payload
        return {
            "id": "swap-id",
            "bip21": "bitcoin:addr",
            "address": "addr",
            "expectedAmount": 1000,
            "timeoutBlockHeight": 123,
            "swapTree": {"claimLeaf": {"txHash": "abc"}},
            "claimPublicKey": "02" + "11" * 32,
        }

    client.request = fake_request

    refund_key, swap = await client.create_swap("lnbc1invoice")

    assert refund_key
    assert swap.id == "swap-id"
    assert swap.redeem_script == (
        '{"swapTree":{"claimLeaf":{"txHash":"abc"}},'
        '"serverPublicKey":"02' + "11" * 32 + '"}'
    )


@pytest.mark.asyncio
async def test_create_reverse_swap_uses_v2_reverse_endpoint(client, v2_pairs):
    submarine_pair, reverse_pair = v2_pairs
    client.limits = reverse_pair["limits"]
    client.pairs = {
        "BTC/BTC": {
            "submarine": submarine_pair,
            "reverse": reverse_pair,
        }
    }

    async def fake_request(method, url, **kwargs):
        assert method == "post"
        assert url == "https://api.boltz.exchange/v2/swap/reverse"
        payload = kwargs["json"]
        assert payload["from"] == "BTC"
        assert payload["to"] == "BTC"
        assert payload["invoiceAmount"] == 50_000
        assert payload["pairHash"] == "reverse-hash"
        assert "claimPublicKey" in payload
        assert "preimageHash" in payload
        return {
            "id": "reverse-id",
            "invoice": "lnbc1holdinvoice",
            "lockupAddress": "addr",
            "onchainAmount": 49_000,
            "timeoutBlockHeight": 123,
            "swapTree": {"claimLeaf": {"txHash": "abc"}},
            "refundPublicKey": "02" + "11" * 32,
        }

    client.request = fake_request

    claim_key, preimage, swap = await client.create_reverse_swap(50_000)

    assert claim_key
    assert preimage
    assert swap.id == "reverse-id"
