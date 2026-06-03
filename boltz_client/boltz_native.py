import importlib
import sys
from importlib.machinery import PathFinder
from importlib.util import module_from_spec
from pathlib import Path
from typing import Any

from embit import ec

from .onchain_taproot import TaprootSwapData

DEFAULT_BITCOIN_ESPLORA_URLS = {
    "main": "https://blockstream.info/api",
    "test": "https://blockstream.info/testnet/api",
    "testnet": "https://blockstream.info/testnet/api",
    "regtest": "http://localhost:3000",
}
DEFAULT_LIQUID_ESPLORA_URLS = {
    "liquidv1": "https://blockstream.info/liquid/api",
    "liquidtestnet": "https://blockstream.info/liquidtestnet/api",
    "elementsregtest": "http://localhost:3001",
}
LIQUID_MIN_TRANSACTION_FEE = 100


class BoltzClientUnavailable(RuntimeError):
    pass


_BOLTZ_CLIENT_MODULE: Any | None = None
_EXTENSION_ROOT = Path(__file__).resolve().parents[1]


def boltz_client_available() -> bool:
    try:
        _boltz_client()
    except BoltzClientUnavailable:
        return False
    return True


async def create_boltz_claim_tx(
    pair: str,
    boltz_id: str,
    lockup_address: str,
    invoice: str,
    receive_address: str,
    privkey_wif: str,
    preimage_hex: str,
    taproot_swap_data: str,
    timeout_block_height: int,
    onchain_amount: int,
    blinding_key: str | None,
    api_url: str,
    network: str,
    esplora_url: str | None,
    fees: int,
) -> str:
    if pair == "L-BTC/BTC" and not blinding_key:
        raise ValueError("Liquid swaps require a Boltz blinding key")
    if not invoice:
        raise ValueError("Reverse swaps require the Boltz invoice")

    bc = _boltz_client()
    keys = _key_pair(bc, privkey_wif)
    response = bc.CreateReverseResponse(
        id=boltz_id,
        invoice=invoice,
        swap_tree=_swap_tree(bc, taproot_swap_data),
        lockup_address=lockup_address,
        refund_public_key=_server_public_key(bc, taproot_swap_data),
        timeout_block_height=timeout_block_height,
        onchain_amount=onchain_amount,
        blinding_key=blinding_key,
    )
    swap_script = bc.SwapScript.from_reverse(
        _chain(bc, pair, network), response, keys.public()
    )
    tx = await swap_script.construct_claim(
        bc.Preimage.from_bytes(bytes.fromhex(preimage_hex)),
        _transaction_params(
            bc=bc,
            output_address=receive_address,
            fees=fees,
            boltz_id=boltz_id,
            keys=keys,
            api_url=api_url,
            pair=pair,
            network=network,
            esplora_url=esplora_url,
        ),
    )
    return tx.hex()


async def create_boltz_refund_tx(
    pair: str,
    boltz_id: str,
    lockup_address: str,
    receive_address: str,
    privkey_wif: str,
    taproot_swap_data: str,
    timeout_block_height: int,
    expected_amount: int,
    blinding_key: str | None,
    api_url: str,
    network: str,
    esplora_url: str | None,
    fees: int,
) -> str:
    if pair == "L-BTC/BTC" and not blinding_key:
        raise ValueError("Liquid swaps require a Boltz blinding key")

    bc = _boltz_client()
    keys = _key_pair(bc, privkey_wif)
    response = bc.CreateSubmarineResponse(
        accept_zero_conf=False,
        address=lockup_address,
        bip21="",
        claim_public_key=_server_public_key(bc, taproot_swap_data),
        expected_amount=expected_amount,
        id=boltz_id,
        referral_id=None,
        swap_tree=_swap_tree(bc, taproot_swap_data),
        timeout_block_height=timeout_block_height,
        blinding_key=blinding_key,
    )
    swap_script = bc.SwapScript.from_submarine(
        _chain(bc, pair, network), response, keys.public()
    )
    tx = await swap_script.construct_refund(
        _transaction_params(
            bc=bc,
            output_address=receive_address,
            fees=fees,
            boltz_id=boltz_id,
            keys=keys,
            api_url=api_url,
            pair=pair,
            network=network,
            esplora_url=esplora_url,
        )
    )
    return tx.hex()


def _boltz_client():
    global _BOLTZ_CLIENT_MODULE
    if _BOLTZ_CLIENT_MODULE:
        return _BOLTZ_CLIENT_MODULE

    try:
        boltz_client = importlib.import_module("boltz_client")
    except (ImportError, OSError) as exc:
        raise BoltzClientUnavailable(
            "Boltz transaction support is not installed. "
            "Install LNbits with the `liquid` extra or `--all-extras`."
        ) from exc
    if not hasattr(boltz_client, "BoltzApiClientV2"):
        boltz_client = _external_boltz_client()
    _BOLTZ_CLIENT_MODULE = boltz_client
    return _BOLTZ_CLIENT_MODULE


def _external_boltz_client():
    search_path = [path for path in sys.path if not _path_matches_extension_root(path)]
    spec = PathFinder.find_spec("boltz_client", search_path)
    if not spec or not spec.loader or not spec.origin:
        raise BoltzClientUnavailable(
            "Boltz transaction support is not installed. "
            "Install LNbits with the `liquid` extra or `--all-extras`."
        )
    origin = Path(spec.origin).resolve()
    if _path_is_relative_to(origin, _EXTENSION_ROOT):
        raise BoltzClientUnavailable(
            "Boltz transaction support is not installed. "
            "Install LNbits with the `liquid` extra or `--all-extras`."
        )
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "BoltzApiClientV2"):
        raise BoltzClientUnavailable(
            "Boltz transaction support is not installed. "
            "Install LNbits with the `liquid` extra or `--all-extras`."
        )
    return module


def _path_matches_extension_root(path: str) -> bool:
    try:
        resolved = Path(path or ".").resolve()
    except OSError:
        return False
    return resolved == _EXTENSION_ROOT


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _network(bc: Any, network: str, pair: str):
    if network in {"main", "liquidv1"}:
        return bc.Network.MAINNET
    if network in {"test", "testnet", "liquidtestnet"}:
        return bc.Network.TESTNET
    if network in {"regtest", "elementsregtest"}:
        return bc.Network.REGTEST
    asset = "Liquid" if pair == "L-BTC/BTC" else "Bitcoin"
    raise ValueError(f"Unsupported {asset} network: {network}")


def _chain(bc: Any, pair: str, network: str):
    if pair == "L-BTC/BTC":
        return bc.lbtc_chain_from_network(_network(bc, network, pair))
    return bc.btc_chain_from_network(_network(bc, network, pair))


def _esplora_url(pair: str, network: str, configured_url: str | None) -> str:
    if configured_url:
        return configured_url.rstrip("/")
    urls = (
        DEFAULT_LIQUID_ESPLORA_URLS
        if pair == "L-BTC/BTC"
        else DEFAULT_BITCOIN_ESPLORA_URLS
    )
    try:
        return urls[network]
    except KeyError as exc:
        asset = "Liquid" if pair == "L-BTC/BTC" else "Bitcoin"
        raise ValueError(f"Unsupported {asset} network: {network}") from exc


def _key_pair(bc: Any, privkey_wif: str):
    private_key = ec.PrivateKey.from_wif(privkey_wif)
    return bc.KeyPair.from_secret_key(bc.SecretKey(private_key.secret.hex()))


def _swap_tree(bc: Any, taproot_swap_data: str):
    swap_data = TaprootSwapData.from_json(taproot_swap_data)
    return bc.SwapTree(
        claim_leaf=_leaf(bc, swap_data.swap_tree["claimLeaf"]),
        refund_leaf=_leaf(bc, swap_data.swap_tree["refundLeaf"]),
    )


def _leaf(bc: Any, leaf: dict[str, Any]):
    return bc.Leaf(output=leaf["output"], version=leaf["version"])


def _server_public_key(bc: Any, taproot_swap_data: str):
    swap_data = TaprootSwapData.from_json(taproot_swap_data)
    return bc.PublicKey(swap_data.server_public_key)


def _transaction_params(
    bc: Any,
    output_address: str,
    fees: int,
    boltz_id: str,
    keys: Any,
    api_url: str,
    pair: str,
    network: str,
    esplora_url: str | None,
):
    connection = bc.ClientConnection.ESPLORA(
        bc.EsploraBuilder(url=_esplora_url(pair, network, esplora_url))
    )
    is_liquid = pair == "L-BTC/BTC"
    chain_client = bc.ChainClient(
        bc.ClientConfig(
            network=_network(bc, network, pair),
            bitcoin=None if is_liquid else connection,
            liquid=connection if is_liquid else None,
        )
    )
    return bc.SwapTransactionParams(
        output_address=output_address,
        fee=bc.Fee.ABSOLUTE(_transaction_fee(pair, fees)),
        swap_id=boltz_id,
        keys=keys,
        chain_client=chain_client,
        boltz_client=bc.BoltzApiClientV2(api_url, 30),
        options=bc.TransactionOptions(cooperative=False),
    )


def _transaction_fee(pair: str, fees: int) -> int:
    if pair == "L-BTC/BTC":
        return max(fees, LIQUID_MIN_TRANSACTION_FEE)
    return fees
