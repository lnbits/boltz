""" boltz_client onchain module """

import os
from hashlib import sha256
from typing import Optional

from embit import ec, script
from embit.networks import NETWORKS
from embit.transaction import SIGHASH, Transaction, TransactionInput, TransactionOutput

from .mempool import LockupData


def create_preimage() -> tuple[str, str]:
    preimage = os.urandom(32)
    preimage_hash = sha256(preimage).hexdigest()
    return preimage.hex(), preimage_hash


def create_key_pair(network) -> tuple[str, str]:
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
    fees: int,
) -> tuple[str, str]:
    # encrypt redeemscript to script_sig
    rs = bytes([34]) + bytes([0]) + bytes([32])
    rs += sha256(bytes.fromhex(redeem_script_hex)).digest()
    script_sig = script.Script(data=rs)
    return create_onchain_tx(
        sequence=0xFFFFFFFE,
        redeem_script_hex=redeem_script_hex,
        privkey_wif=privkey_wif,
        lockup_tx=lockup_tx,
        receive_address=receive_address,
        timeout_block_height=timeout_block_height,
        script_sig=script_sig,
        fees=fees,
    )


def create_claim_tx(
    preimage_hex: str,
    privkey_wif: str,
    receive_address: str,
    redeem_script_hex: str,
    lockup_tx: LockupData,
    fees: int,
) -> tuple[str, str]:
    return create_onchain_tx(
        preimage_hex=preimage_hex,
        lockup_tx=lockup_tx,
        receive_address=receive_address,
        privkey_wif=privkey_wif,
        redeem_script_hex=redeem_script_hex,
        fees=fees,
    )


def create_onchain_tx(
    lockup_tx: LockupData,
    receive_address: str,
    privkey_wif: str,
    redeem_script_hex: str,
    fees: int,
    sequence: int = 0xFFFFFFFF,
    timeout_block_height: int = 0,
    preimage_hex: str = "",
    script_sig: Optional[script.Script] = None,
) -> tuple[str, str]:

    vin = TransactionInput(
        bytes.fromhex(lockup_tx.txid), lockup_tx.vout_cnt, sequence=sequence
    )
    vout = TransactionOutput(
        lockup_tx.vout_amount - fees, script.address_to_scriptpubkey(receive_address)
    )
    tx = Transaction(vin=[vin], vout=[vout])

    if timeout_block_height > 0:
        tx.locktime = timeout_block_height

    if script_sig:
        tx.vin[0].script_sig = script_sig

    # hashing redeemscript

    h = tx.sighash_segwit(
        0, script.Script(data=bytes.fromhex(redeem_script_hex)), lockup_tx.vout_amount
    )

    # sign the redeemscript hash
    sig = ec.PrivateKey.from_wif(privkey_wif).sign(h).serialize() + bytes([SIGHASH.ALL])

    # put the witness into the input
    witness_items = [sig, bytes.fromhex(preimage_hex), bytes.fromhex(redeem_script_hex)]
    tx.vin[0].witness = script.Witness(items=witness_items)

    return bytes.hex(tx.txid()), bytes.hex(tx.serialize())
