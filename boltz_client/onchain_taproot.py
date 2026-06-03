import json
from dataclasses import dataclass
from typing import Any

from embit import compact, ec, hashes, script
from embit.base import EmbitError
from embit.transaction import SIGHASH, Transaction, TransactionInput, TransactionOutput
from embit.util import secp256k1

LEAF_VERSION_TAPSCRIPT = 0xC0


@dataclass
class TaprootSwapData:
    swap_tree: dict[str, Any]
    server_public_key: str

    @classmethod
    def from_json(cls, raw: str) -> "TaprootSwapData":
        data = json.loads(raw)
        return cls(
            swap_tree=data["swapTree"],
            server_public_key=data["serverPublicKey"],
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "swapTree": self.swap_tree,
                "serverPublicKey": self.server_public_key,
            },
            separators=(",", ":"),
        )


def taproot_swap_data_from_response(
    swap_tree: dict[str, Any] | None,
    server_public_key: str | None,
) -> str:
    if not swap_tree or not server_public_key:
        return ""
    return TaprootSwapData(swap_tree, server_public_key).to_json()


def is_taproot_swap_data(raw: str) -> bool:
    try:
        TaprootSwapData.from_json(raw)
        return True
    except (json.JSONDecodeError, KeyError, TypeError):
        return False


def create_taproot_claim_tx(
    lockup_address: str,
    preimage_hex: str,
    privkey_wif: str,
    receive_address: str,
    taproot_swap_data: str,
    lockup_rawtx: str,
    fees: int,
) -> str:
    return create_taproot_tx(
        lockup_address=lockup_address,
        lockup_rawtx=lockup_rawtx,
        receive_address=receive_address,
        privkey_wif=privkey_wif,
        taproot_swap_data=taproot_swap_data,
        fees=fees,
        preimage_hex=preimage_hex,
    )


def create_taproot_refund_tx(
    privkey_wif: str,
    receive_address: str,
    taproot_swap_data: str,
    timeout_block_height: int,
    lockup_address: str,
    lockup_rawtx: str,
    fees: int,
) -> str:
    return create_taproot_tx(
        lockup_address=lockup_address,
        lockup_rawtx=lockup_rawtx,
        receive_address=receive_address,
        privkey_wif=privkey_wif,
        taproot_swap_data=taproot_swap_data,
        fees=fees,
        timeout_block_height=timeout_block_height,
    )


def create_taproot_tx(
    lockup_address: str,
    lockup_rawtx: str,
    receive_address: str,
    privkey_wif: str,
    taproot_swap_data: str,
    fees: int,
    timeout_block_height: int = 0,
    preimage_hex: str = "",
) -> str:
    try:
        lockup_transaction = Transaction.from_string(lockup_rawtx)
    except EmbitError as exc:
        raise ValueError("Invalid lockup transaction hex") from exc

    swap_data = TaprootSwapData.from_json(taproot_swap_data)
    private_key = ec.PrivateKey.from_wif(privkey_wif)
    lockup_script_pubkey = script.address_to_scriptpubkey(lockup_address)
    expected_script_pubkey = _script_pubkey(
        swap_data=swap_data,
        our_public_key=private_key.get_public_key(),
    )
    if lockup_script_pubkey != expected_script_pubkey:
        raise ValueError("Lockup address does not match swap tree")

    vout_amount = None
    vout_index = 0
    for vout in lockup_transaction.vout:
        if vout.script_pubkey == lockup_script_pubkey:
            vout_amount = vout.value
            break
        vout_index += 1

    if vout_amount is None:
        raise ValueError("No matching vout found in lockup transaction")

    if vout_amount <= fees:
        raise ValueError("Fee exceeds lockup transaction output value")

    tx = Transaction(
        vin=[
            TransactionInput(
                lockup_transaction.txid(),
                vout_index,
                sequence=0,
            )
        ],
        vout=[
            TransactionOutput(
                vout_amount - fees,
                script.address_to_scriptpubkey(receive_address),
            )
        ],
    )

    is_refund = preimage_hex == ""
    if timeout_block_height > 0:
        tx.locktime = timeout_block_height

    leaf_script = _leaf_script(swap_data.swap_tree, is_refund)
    leaf_version = _leaf_version(swap_data.swap_tree, is_refund)
    script_pubkeys = [lockup_script_pubkey]
    values = [vout_amount]

    sighash = tx.sighash_taproot(
        0,
        script_pubkeys,
        values,
        sighash=SIGHASH.DEFAULT,
        ext_flag=1,
        script=leaf_script,
        leaf_version=leaf_version,
    )
    signature = private_key.schnorr_sign(sighash).serialize()
    witness = [signature]
    if not is_refund:
        witness.append(bytes.fromhex(preimage_hex))
    witness.extend(
        [
            leaf_script.data,
            _control_block(
                swap_data=swap_data,
                our_public_key=private_key.get_public_key(),
                is_refund=is_refund,
            ),
        ]
    )
    tx.vin[0].witness.items = witness
    return tx.serialize().hex()


def _leaf_script(swap_tree: dict[str, Any], is_refund: bool) -> script.Script:
    leaf = _leaf(swap_tree, is_refund)
    return script.Script(bytes.fromhex(leaf["output"]))


def _leaf_version(swap_tree: dict[str, Any], is_refund: bool) -> int:
    return int(_leaf(swap_tree, is_refund).get("version", LEAF_VERSION_TAPSCRIPT))


def _leaf(swap_tree: dict[str, Any], is_refund: bool) -> dict[str, Any]:
    return swap_tree["refundLeaf" if is_refund else "claimLeaf"]


def _control_block(
    swap_data: TaprootSwapData,
    our_public_key: ec.PublicKey,
    is_refund: bool,
) -> bytes:
    internal_key = _aggregate_internal_key(
        bytes.fromhex(swap_data.server_public_key),
        our_public_key,
    )
    claim_hash = _tapleaf_hash(
        _leaf_script(swap_data.swap_tree, False),
        _leaf_version(swap_data.swap_tree, False),
    )
    refund_hash = _tapleaf_hash(
        _leaf_script(swap_data.swap_tree, True),
        _leaf_version(swap_data.swap_tree, True),
    )
    merkle_root = _tapbranch_hash(claim_hash, refund_hash)
    output_parity = _tweaked_public_key_parity(internal_key, merkle_root)
    leaf_version = _leaf_version(swap_data.swap_tree, is_refund)
    sibling_hash = claim_hash if is_refund else refund_hash
    return bytes([leaf_version | output_parity]) + internal_key.xonly() + sibling_hash


def _script_pubkey(
    swap_data: TaprootSwapData,
    our_public_key: ec.PublicKey,
) -> script.Script:
    internal_key = _aggregate_internal_key(
        bytes.fromhex(swap_data.server_public_key),
        our_public_key,
    )
    merkle_root = _tapbranch_hash(
        _tapleaf_hash(
            _leaf_script(swap_data.swap_tree, False),
            _leaf_version(swap_data.swap_tree, False),
        ),
        _tapleaf_hash(
            _leaf_script(swap_data.swap_tree, True),
            _leaf_version(swap_data.swap_tree, True),
        ),
    )
    return script.p2tr(internal_key, _TaprootTree(merkle_root))


class _TaprootTree:
    def __init__(self, merkle_root: bytes):
        self.merkle_root = merkle_root

    def tweak(self) -> bytes:
        return self.merkle_root


def _aggregate_internal_key(
    server_public_key: bytes,
    our_public_key: ec.PublicKey,
) -> ec.PublicKey:
    server_key = ec.PublicKey.parse(server_public_key)
    aggregate_point = secp256k1.musig_pubkey_combine(
        server_key._point,
        our_public_key._point,
    )
    return ec.PublicKey(aggregate_point)


def _tweaked_public_key_parity(internal_key: ec.PublicKey, merkle_root: bytes) -> int:
    tweak = hashes.tagged_hash("TapTweak", internal_key.xonly() + merkle_root)
    point = secp256k1.ec_pubkey_parse(b"\x02" + internal_key.xonly())
    secp256k1.ec_pubkey_tweak_add(point, tweak)
    return 1 if secp256k1.ec_pubkey_serialize(point)[0] == 0x03 else 0


def _tapleaf_hash(leaf_script: script.Script, leaf_version: int) -> bytes:
    return hashes.tagged_hash(
        "TapLeaf",
        bytes([leaf_version])
        + compact.to_bytes(len(leaf_script.data))
        + leaf_script.data,
    )


def _tapbranch_hash(left: bytes, right: bytes) -> bytes:
    if right < left:
        left, right = right, left
    return hashes.tagged_hash("TapBranch", left + right)
