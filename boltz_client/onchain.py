""" boltz_client onchain module """
import os
from hashlib import sha256
from typing import Optional

from embit import ec, script
from embit.base import EmbitError
from embit.liquid.addresses import to_unconfidential
from embit.liquid.networks import NETWORKS as LNETWORKS
from embit.liquid.transaction import LTransaction
from embit.networks import NETWORKS
from embit.transaction import SIGHASH, Transaction, TransactionInput, TransactionOutput

from .mempool import LockupData
from .onchain_wally import create_liquid_tx


def get_txid(tx_hex: str, pair: str = "BTC/BTC") -> str:
    try:
        if pair == "L-BTC/BTC":
            tx = LTransaction.from_string(tx_hex)
        else:
            tx = Transaction.from_string(tx_hex)
        return tx.txid().hex()
    except EmbitError as exc:
        raise ValueError("Invalid transaction hex") from exc


def validate_address(address: str, network: str, pair: str) -> str:
    if pair == "L-BTC/BTC":
        net = LNETWORKS[network]
        _address_unconfidential = to_unconfidential(address)
        if not _address_unconfidential:
            raise ValueError("can not unconfidentialize address")
        address = _address_unconfidential
        _address = _address_unconfidential
    else:
        net = NETWORKS[network]
        _address = address
    try:
        addr = script.Script.from_address(_address) or script.Script()
        if addr.address(net) != address:
            raise ValueError(f"Invalid network {network}")
        return address
    except EmbitError as exc:
        raise ValueError(f"Invalid address: {exc}") from exc


def create_preimage() -> tuple[str, str]:
    preimage = os.urandom(32)
    preimage_hash = sha256(preimage).hexdigest()
    return preimage.hex(), preimage_hash


def create_key_pair(network, pair) -> tuple[str, str]:
    if pair == "L-BTC/BTC":
        net = LNETWORKS[network]
    else:
        net = NETWORKS[network]

    privkey = ec.PrivateKey(os.urandom(32), True, net)
    pubkey_hex = bytes.hex(privkey.sec())
    privkey_wif = privkey.wif(net)
    return privkey_wif, pubkey_hex


def create_refund_tx(
    privkey_wif: str,
    receive_address: str,
    redeem_script_hex: str,
    timeout_block_height: int,
    lockup_tx: LockupData,
    pair: str,
    fees: int,
    blinding_key: Optional[str] = None,
) -> str:
    # redeemscript to script_sig
    rs = bytes([34]) + bytes([0]) + bytes([32])
    rs += sha256(bytes.fromhex(redeem_script_hex)).digest()
    script_sig = rs
    return create_onchain_tx(
        sequence=0xFFFFFFFE,
        redeem_script_hex=redeem_script_hex,
        privkey_wif=privkey_wif,
        lockup_tx=lockup_tx,
        receive_address=receive_address,
        timeout_block_height=timeout_block_height,
        script_sig=script_sig,
        pair=pair,
        fees=fees,
        blinding_key=blinding_key,
    )


def create_claim_tx(
    preimage_hex: str,
    privkey_wif: str,
    receive_address: str,
    redeem_script_hex: str,
    lockup_tx: LockupData,
    fees: int,
    pair: str,
    blinding_key: Optional[str] = None,
) -> str:
    return create_onchain_tx(
        preimage_hex=preimage_hex,
        lockup_tx=lockup_tx,
        receive_address=receive_address,
        privkey_wif=privkey_wif,
        redeem_script_hex=redeem_script_hex,
        fees=fees,
        pair=pair,
        blinding_key=blinding_key,
    )


def create_onchain_tx(
    lockup_tx: LockupData,
    receive_address: str,
    privkey_wif: str,
    redeem_script_hex: str,
    fees: int,
    pair: str,
    sequence: int = 0xFFFFFFFF,
    timeout_block_height: int = 0,
    preimage_hex: str = "",
    script_sig: Optional[bytes] = None,
    blinding_key: Optional[str] = None,
) -> str:

    if pair == "L-BTC/BTC":
        if not blinding_key:
            raise ValueError("Blinding key is required for L-BTC/BTC pair")

        return create_liquid_tx(
            lockup_tx=lockup_tx,
            receive_address=receive_address,
            privkey_wif=privkey_wif,
            redeem_script_hex=redeem_script_hex,
            fees=fees,
            sequence=sequence,
            timeout_block_height=timeout_block_height,
            preimage_hex=preimage_hex,
            blinding_key=blinding_key,
        )

    vout = TransactionOutput(
        lockup_tx.vout_amount - fees,
        script.address_to_scriptpubkey(receive_address),
    )
    vout = [vout]
    vin = TransactionInput(
        bytes.fromhex(lockup_tx.txid),
        lockup_tx.vout_cnt,
        sequence=sequence,
        script_sig=script.Script(data=script_sig) if script_sig else None,
    )
    tx = Transaction(vin=[vin], vout=vout)

    if timeout_block_height > 0:
        tx.locktime = timeout_block_height

    redeem_script = script.Script(data=bytes.fromhex(redeem_script_hex))
    h = tx.sighash_segwit(0, redeem_script, lockup_tx.vout_amount)
    sig = ec.PrivateKey.from_wif(privkey_wif).sign(h).serialize() + bytes([SIGHASH.ALL])
    witness_script = script.Witness(
        items=[sig, bytes.fromhex(preimage_hex), bytes.fromhex(redeem_script_hex)]
    )

    tx.vin[0].witness = witness_script
    if script_sig:
        tx.vin[0].script_sig = script.Script(data=script_sig)

    return bytes.hex(tx.serialize())
