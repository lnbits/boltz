from typing import Any

from embit import ec

from .onchain_taproot import TaprootSwapData

DEFAULT_LIQUID_ESPLORA_URLS = {
    "liquidv1": "https://blockstream.info/liquid/api",
    "liquidtestnet": "https://blockstream.info/liquidtestnet/api",
    "elementsregtest": "http://localhost:3001",
}


class LiquidClientUnavailable(RuntimeError):
    pass


def liquid_client_available() -> bool:
    try:
        _boltz_client()
    except LiquidClientUnavailable:
        return False
    return True


async def create_liquid_claim_tx(
    boltz_id: str,
    lockup_address: str,
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
    bc = _boltz_client()
    keys = _key_pair(bc, privkey_wif)
    response = bc.CreateReverseResponse(
        id=boltz_id,
        invoice=None,
        swap_tree=_swap_tree(bc, taproot_swap_data),
        lockup_address=lockup_address,
        refund_public_key=_server_public_key(bc, taproot_swap_data),
        timeout_block_height=timeout_block_height,
        onchain_amount=onchain_amount,
        blinding_key=blinding_key,
    )
    swap_script = bc.SwapScript.from_reverse(
        _liquid_chain(bc, network), response, keys.public()
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
            network=network,
            esplora_url=esplora_url,
        ),
    )
    return tx.hex()


async def create_liquid_refund_tx(
    boltz_id: str,
    lockup_address: str,
    receive_address: str,
    privkey_wif: str,
    taproot_swap_data: str,
    timeout_block_height: int,
    blinding_key: str | None,
    api_url: str,
    network: str,
    esplora_url: str | None,
    fees: int,
) -> str:
    bc = _boltz_client()
    keys = _key_pair(bc, privkey_wif)
    response = bc.CreateSubmarineResponse(
        accept_zero_conf=False,
        address=lockup_address,
        bip21="",
        claim_public_key=_server_public_key(bc, taproot_swap_data),
        expected_amount=0,
        id=boltz_id,
        referral_id=None,
        swap_tree=_swap_tree(bc, taproot_swap_data),
        timeout_block_height=timeout_block_height,
        blinding_key=blinding_key,
    )
    swap_script = bc.SwapScript.from_submarine(
        _liquid_chain(bc, network), response, keys.public()
    )
    tx = await swap_script.construct_refund(
        _transaction_params(
            bc=bc,
            output_address=receive_address,
            fees=fees,
            boltz_id=boltz_id,
            keys=keys,
            api_url=api_url,
            network=network,
            esplora_url=esplora_url,
        )
    )
    return tx.hex()


def _boltz_client():
    try:
        import boltz_client
    except (ImportError, OSError) as exc:
        raise LiquidClientUnavailable(
            "Optional Liquid support is not installed. "
            "Install LNbits with the `liquid` extra."
        ) from exc
    if not hasattr(boltz_client, "BoltzApiClientV2"):
        raise LiquidClientUnavailable(
            "Optional Liquid support is not installed. "
            "Install LNbits with the `liquid` extra."
        )
    return boltz_client


def _network(bc: Any, network: str):
    if network == "liquidv1":
        return bc.Network.MAINNET
    if network == "liquidtestnet":
        return bc.Network.TESTNET
    if network == "elementsregtest":
        return bc.Network.REGTEST
    raise ValueError(f"Unsupported Liquid network: {network}")


def _liquid_chain(bc: Any, network: str):
    return bc.lbtc_chain_from_network(_network(bc, network))


def _esplora_url(network: str, configured_url: str | None) -> str:
    if configured_url:
        return configured_url.rstrip("/")
    try:
        return DEFAULT_LIQUID_ESPLORA_URLS[network]
    except KeyError as exc:
        raise ValueError(f"Unsupported Liquid network: {network}") from exc


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
    network: str,
    esplora_url: str | None,
):
    connection = bc.ClientConnection.ESPLORA(
        bc.EsploraBuilder(url=_esplora_url(network, esplora_url))
    )
    chain_client = bc.ChainClient(
        bc.ClientConfig(network=_network(bc, network), bitcoin=None, liquid=connection)
    )
    return bc.SwapTransactionParams(
        output_address=output_address,
        fee=bc.Fee.ABSOLUTE(fees),
        swap_id=boltz_id,
        keys=keys,
        chain_client=chain_client,
        boltz_client=bc.BoltzApiClientV2(api_url, 30),
        options=bc.TransactionOptions(cooperative=False),
    )
