""" boltz_client onchain module """
import os
from hashlib import sha256
from typing import Optional

from embit import ec, script
from embit.base import EmbitError
from embit.liquid.addresses import to_unconfidential
from embit.liquid.networks import NETWORKS as LNETWORKS
from embit.networks import NETWORKS
from embit.transaction import SIGHASH, Transaction, TransactionInput, TransactionOutput

from .onchain_wally import create_liquid_tx


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
    lockup_address: str,
    lockup_rawtx: str,
    pair: str,
    fees: int,
    blinding_key: Optional[str] = None,
) -> str:
    # redeemscript to script_sig
    rs = bytes([34]) + bytes([0]) + bytes([32])
    rs += sha256(bytes.fromhex(redeem_script_hex)).digest()
    script_sig = rs
    return create_onchain_tx(
        lockup_address=lockup_address,
        sequence=0xFFFFFFFE,
        redeem_script_hex=redeem_script_hex,
        privkey_wif=privkey_wif,
        lockup_rawtx=lockup_rawtx,
        receive_address=receive_address,
        timeout_block_height=timeout_block_height,
        script_sig=script_sig,
        pair=pair,
        fees=fees,
        blinding_key=blinding_key,
    )


def create_claim_tx(
    lockup_address: str,
    preimage_hex: str,
    privkey_wif: str,
    receive_address: str,
    redeem_script_hex: str,
    lockup_rawtx: str,
    fees: int,
    pair: str,
    blinding_key: Optional[str] = None,
) -> str:
    return create_onchain_tx(
        lockup_address=lockup_address,
        preimage_hex=preimage_hex,
        lockup_rawtx=lockup_rawtx,
        receive_address=receive_address,
        privkey_wif=privkey_wif,
        redeem_script_hex=redeem_script_hex,
        fees=fees,
        pair=pair,
        blinding_key=blinding_key,
    )


def create_onchain_tx(
    lockup_address: str,
    lockup_rawtx: str,
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
            lockup_rawtx=lockup_rawtx,
            lockup_address=lockup_address,
            receive_address=receive_address,
            privkey_wif=privkey_wif,
            redeem_script_hex=redeem_script_hex,
            fees=fees,
            sequence=sequence,
            timeout_block_height=timeout_block_height,
            preimage_hex=preimage_hex,
            blinding_key=blinding_key,
        )

    try:
        lockup_transaction = Transaction.from_string(lockup_rawtx)
    except EmbitError as exc:
        raise ValueError("Invalid lockup transaction hex") from exc

    txid = lockup_transaction.txid()
    vout_amount: Optional[int] = None
    vout_index: int = 0
    for vout in lockup_transaction.vout:
        if vout.script_pubkey == script.address_to_scriptpubkey(lockup_address):
            vout_amount = vout.value
            break
        vout_index += 1

    if vout_amount is None:
        raise ValueError("No matching vout found in lockup transaction")

    vout = TransactionOutput(
        vout_amount - fees,
        script.address_to_scriptpubkey(receive_address),
    )
    vout = [vout]
    vin = TransactionInput(
        txid,
        vout_index,
        sequence=sequence,
        script_sig=script.Script(data=script_sig) if script_sig else None,
    )
    tx = Transaction(vin=[vin], vout=vout)

    if timeout_block_height > 0:
        tx.locktime = timeout_block_height

    redeem_script = script.Script(data=bytes.fromhex(redeem_script_hex))
    h = tx.sighash_segwit(0, redeem_script, vout_amount)
    sig = ec.PrivateKey.from_wif(privkey_wif).sign(h).serialize() + bytes([SIGHASH.ALL])
    witness_script = script.Witness(
        items=[sig, bytes.fromhex(preimage_hex), bytes.fromhex(redeem_script_hex)]
    )

    tx.vin[0].witness = witness_script
    if script_sig:
        tx.vin[0].script_sig = script.Script(data=script_sig)

    return bytes.hex(tx.serialize())
