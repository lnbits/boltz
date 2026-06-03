"""boltz_client onchain module"""

import os
from hashlib import sha256

from embit import ec, script
from embit.liquid.addresses import to_unconfidential
from embit.liquid.networks import NETWORKS as LNETWORKS
from embit.networks import NETWORKS


def validate_address(address: str, network: str, pair: str) -> str:
    if pair == "L-BTC/BTC":
        net = LNETWORKS[network]
        _address_unconfidential = to_unconfidential(address)
        if not _address_unconfidential:
            raise ValueError("can not unconfidentialize address")
        address = _address_unconfidential
        _address = _address_unconfidential
    else:
        if network == "testnet":
            network = "test"
        net = NETWORKS[network]

    _address = address
    addr = script.Script.from_address(_address) or script.Script()
    if addr.address(net) != address:
        raise ValueError(f"Invalid network {network}")
    return address


def create_preimage() -> tuple[str, str]:
    preimage = os.urandom(32)
    preimage_hash = sha256(preimage).hexdigest()
    return preimage.hex(), preimage_hash


def create_key_pair(network, pair) -> tuple[str, str]:
    if pair == "L-BTC/BTC":
        net = LNETWORKS[network]
    else:
        if network == "testnet":
            network = "test"
        net = NETWORKS[network]

    privkey = ec.PrivateKey(os.urandom(32), True, net)
    pubkey_hex = bytes.hex(privkey.sec())
    privkey_wif = privkey.wif(net)
    return privkey_wif, pubkey_hex
