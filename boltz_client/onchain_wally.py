"""
https://github.com/jgriffiths/wally_swap_test/blob/master/main.py
https://wally.readthedocs.io/en/release_0.8.9/psbt/
https://github.com/BlockchainCommons/Learning-Bitcoin-from-the-Command-Line/blob/master/07_1_Creating_a_Partially_Signed_Bitcoin_Transaction.md
special thanks to @jgriffiths for helping debugging this!
"""
from __future__ import annotations

import secrets
from typing import Optional

from .mempool import LockupData


def get_entropy(num_outputs_to_blind):
    # For each output to blind, we need 32 bytes of entropy for each of:
    # - Output assetblinder
    # - Output amountblinder
    # - Ephemeral rangeproof ECDH key
    # - Explicit value rangeproof
    # - Surjectionproof seed
    return secrets.token_bytes(num_outputs_to_blind * 5 * 32)


def create_liquid_tx(
    lockup_tx: LockupData,
    receive_address: str,
    privkey_wif: str,
    redeem_script_hex: str,
    fees: int,
    sequence: int = 0xFFFFFFFF,
    timeout_block_height: int = 0,
    preimage_hex: str = "",
    blinding_key: Optional[str] = None,
) -> str:

    try:
        import wallycore as wally
    except ImportError as exc:
        raise ImportError(
            "`wallycore` is not installed, but required for liquid support."
        ) from exc

    if receive_address.startswith("ert") or receive_address.startswith("el"):
        lasset_hex = "5ac9f65c0efcc4775e0baec4ec03abdde22473cd3cf33c0419ca290e0751b225"
        confidential_addr_prefix = "ert"
        confidential_addr_family = "el"
    elif receive_address.startswith("tex") or receive_address.startswith("tlq"):
        lasset_hex = "144c654344aa716d6f3abcc1ca90e5641e4e2a7f633bc09fe3baf64585819a49"
        confidential_addr_prefix = "tex"
        confidential_addr_family = "tlq"
    elif receive_address.startswith("ex") or receive_address.startswith("lq"):
        lasset_hex = "6f0279e9ed041c3d710a9f57d0c02928416460c4b722ae3457a11eec381c526d"
        confidential_addr_prefix = "ex"
        confidential_addr_family = "lq"
    else:
        raise ValueError(f"Unknown prefix: {receive_address[:3]}")

    LASSET = bytes.fromhex(lasset_hex)[::-1]

    redeem_script = bytes.fromhex(redeem_script_hex)
    preimage = bytes.fromhex(preimage_hex)
    private_key = wally.wif_to_bytes(
        privkey_wif,
        wally.WALLY_ADDRESS_VERSION_WIF_TESTNET,
        wally.WALLY_WIF_FLAG_COMPRESSED,
    )  # type: ignore

    assert blinding_key, "blinding_key is required"
    try:
        blinding_key_bytes = bytes.fromhex(blinding_key)
    except ValueError as exc:
        raise ValueError("blinding_key must be hex encoded") from exc

    receive_blinding_pubkey = wally.confidential_addr_segwit_to_ec_public_key(
        receive_address, confidential_addr_family
    )  # type: ignore
    receive_unconfidential_address = wally.confidential_addr_to_addr_segwit(
        receive_address, confidential_addr_family, confidential_addr_prefix
    )  # type: ignore
    receive_script_pubkey = wally.addr_segwit_to_bytes(
        receive_unconfidential_address, confidential_addr_prefix, 0
    )  # type: ignore

    # parse lockup tx
    lockup_transaction = wally.tx_from_hex(
        lockup_tx.tx_hex, wally.WALLY_TX_FLAG_USE_ELEMENTS
    )
    vout_n: Optional[int] = None
    for vout in range(wally.tx_get_num_outputs(lockup_transaction)):
        script_out = wally.tx_get_output_script(lockup_transaction, vout)  # type: ignore
        pub_key = wally.addr_segwit_from_bytes(script_out, confidential_addr_prefix, 0)
        if pub_key == lockup_tx.script_pub_key:
            vout_n = vout
            break

    assert vout_n is not None, "Lockup vout not found"

    txid = wally.tx_get_txid(lockup_transaction)  # type: ignore
    lockup_script = wally.tx_get_output_script(lockup_transaction, vout_n)  # type: ignore
    lockup_rangeproof = wally.tx_get_output_rangeproof(lockup_transaction, vout_n)  # type: ignore
    lockup_ephemeral_pubkey = wally.tx_get_output_nonce(lockup_transaction, vout_n)  # type: ignore
    lockup_asset_commitment = wally.tx_get_output_asset(lockup_transaction, vout_n)  # type: ignore
    lockup_value_commitment = wally.tx_get_output_value(lockup_transaction, vout_n)  # type: ignore

    # UNBLIND
    unblinded_amount, unblinded_asset, abf, vbf = wally.asset_unblind(
        lockup_ephemeral_pubkey,
        blinding_key_bytes,
        lockup_rangeproof,
        lockup_value_commitment,
        lockup_script,
        lockup_asset_commitment,
    )  # type: ignore

    assert unblinded_asset == LASSET, "Wrong asset"

    # INITIALIZE PSBT (PSET)
    num_vin = 1
    num_vout = 2
    psbt_flags = wally.WALLY_PSBT_INIT_PSET  # Make an Elements PSET
    psbt_version = wally.WALLY_PSBT_VERSION_2  # PSET only supports v2
    psbt = wally.psbt_init(psbt_version, num_vin, num_vout, 0, psbt_flags)

    if timeout_block_height > 0:
        wally.psbt_set_fallback_locktime(psbt, timeout_block_height)

    # ADD PSBT INPUT
    idx = wally.psbt_get_num_inputs(psbt)
    # Add the txout from the lockup tx as the witness UTXO for our input
    input_ = wally.tx_input_init(txid, vout_n, sequence, None, None)
    wally.psbt_add_tx_input_at(psbt, idx, 0, input_)
    wally.psbt_set_input_witness_utxo_from_tx(psbt, idx, lockup_transaction, vout_n)
    # Add the rangeproof
    wally.psbt_set_input_utxo_rangeproof(psbt, idx, lockup_rangeproof)
    # And the witness script
    wally.psbt_set_input_witness_script(psbt, idx, redeem_script)
    # Add the key info for our private key, so psbt_sign knows what input
    # to sign when given the private key.
    # Since we don't have a BIP32 key, add it with a dummy fingerprint and path.
    # When signing with a non-BIP32 private key, wally uses the key as given
    # and doesn't attempt to derive a BIP32 key to sign with, so these dummy
    # values aren't used except to indicate that the key belongs to this input.
    keypaths = wally.map_keypath_public_key_init(1)
    signing_pubkey = wally.ec_public_key_from_private_key(private_key)  # type: ignore
    wally.map_keypath_add(keypaths, signing_pubkey, bytes(4), [0])
    wally.psbt_set_input_keypaths(psbt, idx, keypaths)

    # Uncomment to generate explicit value proofs for the input.
    # These expose the unblinded value and asset in the PSBT; we
    # don't need them for this use-case.
    # wally.psbt_generate_input_explicit_proofs(psbt, idx, unblinded_amount,
    # unblinded_asset, abf, vbf, secrets.token_bytes(32))

    # ADD PSBT OUTPUT
    output_idx = wally.psbt_get_num_outputs(psbt)
    asset_tag = bytearray([1]) + unblinded_asset  # Explicit (unblinded) asset
    value = wally.tx_confidential_value_from_satoshi(unblinded_amount - fees)  # type: ignore
    txout = wally.tx_elements_output_init(receive_script_pubkey, asset_tag, value, None)
    wally.psbt_add_tx_output_at(psbt, output_idx, 0, txout)
    wally.psbt_set_output_blinding_public_key(psbt, output_idx, receive_blinding_pubkey)
    wally.psbt_set_output_blinder_index(psbt, output_idx, 0)

    # ADD FEE OUTPUT
    fee_value = wally.tx_confidential_value_from_satoshi(fees)  # type: ignore
    fee_txout = wally.tx_elements_output_init(None, asset_tag, fee_value)
    wally.psbt_add_tx_output_at(psbt, output_idx + 1, 0, fee_txout)

    # BLIND PSBT
    entropy = get_entropy(1)
    values, vbfs, assets, abfs = [wally.map_init(1, None) for _ in range(4)]

    unblinded_value = wally.tx_confidential_value_from_satoshi(unblinded_amount)  # type: ignore
    wally.map_add_integer(values, idx, unblinded_value)
    wally.map_add_integer(vbfs, idx, vbf)
    wally.map_add_integer(assets, idx, unblinded_asset)
    wally.map_add_integer(abfs, idx, abf)

    # ephemeral_keys
    _ = wally.psbt_blind(psbt, values, vbfs, assets, abfs, entropy, output_idx, 0)

    # SIGN PSBT
    # wally can identify the input to sign because we gave the keypath above
    wally.psbt_sign(psbt, private_key, wally.EC_FLAG_ECDSA)
    # Fetch the signature from the PSBT input for finalization
    sig_pos = wally.psbt_find_input_signature(psbt, idx, signing_pubkey)
    assert sig_pos != 0, "signature not found"
    sig = wally.psbt_get_input_signature(psbt, idx, sig_pos - 1)  # type: ignore

    # FINALIZE PSBT
    # Wally can't know how to finalize our bespoke p2wsh input, so
    # we do it manually:
    # 1) Set the final_witness according to our script requirements
    stack = wally.tx_witness_stack_init(3)
    wally.tx_witness_stack_add(stack, sig)
    wally.tx_witness_stack_add(stack, preimage)
    wally.tx_witness_stack_add(stack, redeem_script)
    wally.psbt_set_input_final_witness(psbt, idx, stack)
    # 2) Set the final_scriptsig. For p2wsh this must be empty, so
    #    we don't have to do anything.
    # if script_sig:
    #     wally.psbt_set_input_final_scriptsig(psbt, idx, script_sig)

    # OUTPUT FINALIZED PSBT/TX
    # Convert the PSBT to base64, then parse in strict mode.
    # This uses wally to perform strict verification that everything is OK.
    base64 = wally.psbt_to_base64(psbt, 0)
    wally.psbt_from_base64(base64, wally.WALLY_PSBT_PARSE_FLAG_STRICT)
    # Dump the psbt. To extract the finalized tx, use e.g:
    # elements-cli-sim finalizepsbt $(python psbt_wally.py) true
    # print(base64)

    # Extract the completed tx from the now-finalized psbt
    tx = wally.psbt_extract(psbt, 0)  # 0 == must be finalized

    rawtx = str(wally.tx_to_hex(tx, wally.WALLY_TX_FLAG_USE_WITNESS))

    return rawtx
