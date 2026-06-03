import pytest

from ..boltz_client.boltz import BoltzClient, BoltzConfig
from ..boltz_client.liquid import liquid_client_available
from ..boltz_client.onchain_taproot import (
    TaprootSwapData,
    is_taproot_swap_data,
    taproot_swap_data_from_response,
)


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


def test_liquid_client_detection_requires_pypi_boltz_client(monkeypatch):
    import builtins
    import types

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "boltz_client":
            return types.SimpleNamespace()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert not liquid_client_available()


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
