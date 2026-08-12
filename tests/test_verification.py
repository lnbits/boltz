"""
Unit tests for BoltzClient swap response verification.

Test patterns follow Electrum's tests/test_submarine_swaps.py, adapted
from integration-level (ToyServer) to unit-level (direct function calls).

Source references per test:
- https://github.com/spesmilo/electrum/blob/master/tests/test_submarine_swaps.py
- https://github.com/BoltzExchange/boltz-backend/blob/master/docs/dont-trust-verify.md
- https://github.com/spesmilo/electrum/pull/10827 (additional hardening)
"""

import hashlib
import secrets

import pytest

from boltz_client.boltz import (
    BoltzClient,
    BoltzVerificationException,
    BoltzReverseSwapResponse,
)


def _build_redeem_script(preimage_hash_hex: str, claim_pubkey_hex: str, timeout: int = 1000144) -> str:
    try:
        ph160 = hashlib.new("ripemd160", bytes.fromhex(preimage_hash_hex)).digest()
    except Exception:
        import struct

        def _rol32(x, r): return ((x << r) | (x >> (32 - r))) & 0xFFFFFFFF
        def ripemd160(msg):
            ml=len(msg)*8; msg+=b'\x80'
            while len(msg)%64!=56: msg+=b'\x00'
            msg+=ml.to_bytes(8,'little')
            rl=[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,7,4,13,1,10,6,15,3,12,0,9,5,2,14,11,8,3,10,14,4,9,15,8,1,2,7,0,6,13,11,5,12,1,9,11,10,0,8,12,4,13,3,7,15,14,5,6,2,4,0,5,9,7,12,2,10,14,1,3,8,11,6,15,13]
            rr=[5,14,7,0,9,2,11,4,13,6,15,8,1,10,3,12,6,11,3,7,0,13,5,10,14,15,8,12,4,9,1,2,15,5,1,3,7,14,6,9,11,8,12,2,10,0,4,13,8,6,4,1,3,11,15,0,5,12,2,13,9,7,10,14,12,15,10,4,1,5,8,7,6,2,13,14,0,3,9,11]
            sl=[11,14,15,12,5,8,7,9,11,13,14,15,6,7,9,8,7,6,8,13,11,9,7,15,7,12,15,9,11,7,13,12,11,13,6,7,14,9,13,15,14,8,13,6,5,12,7,5,11,12,14,15,14,15,9,8,9,14,5,6,8,6,5,12,9,15,5,11,6,8,13,12,5,12,13,14,11,8,5,6]
            sr=[8,9,9,11,13,15,15,5,7,7,8,11,14,14,12,6,9,13,15,7,12,8,9,11,7,7,12,7,6,15,13,11,9,7,15,11,8,6,6,14,12,13,5,14,13,13,7,5,15,5,8,11,14,14,6,14,6,9,12,9,12,5,15,8,8,5,12,9,12,5,14,6,8,13,6,5,15,13,11,11]
            Kl=[0,0x5A827999,0x6ED9EBA1,0x8F1BBCDC,0xA953FD4E]; Kr=[0x50A28BE6,0x5C4DD124,0x6D703EF3,0x7A6D76E9,0]
            def f(j,x,y,z):
                if j<16:return x^y^z
                if j<32:return(x&y)|(~x&0xFFFFFFFF&z)
                if j<48:return(x|(~y&0xFFFFFFFF))^z
                if j<64:return(x&z)|(y&(~z&0xFFFFFFFF))
                return x^(y|(~z&0xFFFFFFFF))
            h=[0x67452301,0xEFCDAB89,0x98BADCFE,0x10325476,0xC3D2E1F0]
            for cs in range(0,len(msg),64):
                X=list(struct.unpack('<16I',msg[cs:cs+64])); a,b,c,d,e=h; a2,b2,c2,d2,e2=h
                for j in range(80):
                    ri=j//16
                    t=(_rol32((a+f(j,b,c,d)+X[rl[j]]+Kl[ri])&0xFFFFFFFF,sl[j])+e)&0xFFFFFFFF; a,e,d,c,b=e,d,_rol32(c,10),b,t
                    t=(_rol32((a2+f(79-j,b2,c2,d2)+X[rr[j]]+Kr[ri])&0xFFFFFFFF,sr[j])+e2)&0xFFFFFFFF; a2,e2,d2,c2,b2=e2,d2,_rol32(c2,10),b2,t
                h[0]=(h[1]+c+d2)&0xFFFFFFFF;h[1]=(h[2]+d+e2)&0xFFFFFFFF;h[2]=(h[3]+e+a2)&0xFFFFFFFF;h[3]=(h[4]+a+b2)&0xFFFFFFFF;h[4]=(h[0]&0xFFFFFFFF+h[0]-h[0]);h[0]=h[0]
            return struct.pack('<5I',*h)
        ph160 = ripemd160(bytes.fromhex(preimage_hash_hex))

    cpk = bytes.fromhex(claim_pubkey_hex)
    rpk = bytes([2]) + secrets.token_bytes(32)
    s = bytearray([0x82,1,0x20,0x87,0x63,0xa9,0x14])
    s.extend(ph160)
    s.extend([0x88,0x21]); s.extend(cpk)
    s.extend([0x67,0x75,0x03]); s.extend(timeout.to_bytes(3,'little'))
    s.extend([0xb1,0x75,0x21]); s.extend(rpk); s.extend([0x68,0xac])
    return bytes(s).hex()


@pytest.fixture
def client():
    return BoltzClient.__new__(BoltzClient)


@pytest.fixture
def swap_data():
    preimage = secrets.token_bytes(32)
    preimage_hash = hashlib.sha256(preimage).digest().hex()
    claim_pubkey = bytes([2]) + secrets.token_bytes(32)
    claim_pubkey_hex = claim_pubkey.hex()
    redeem_script = _build_redeem_script(preimage_hash, claim_pubkey_hex)
    return {
        "preimage_hash": preimage_hash,
        "claim_pubkey_hex": claim_pubkey_hex,
        "redeem_script": redeem_script,
        "attacker_hash": hashlib.sha256(secrets.token_bytes(32)).digest().hex(),
    }


def _make_swap(swap_data, **kwargs):
    defaults = dict(
        id="test-swap",
        invoice="lnbc...",
        redeemScript=swap_data["redeem_script"],
        lockupAddress="bcrt1qtest",
        timeoutBlockHeight=1000144,
        onchainAmount=99000,
    )
    defaults.update(kwargs)
    return BoltzReverseSwapResponse(**defaults)


def test_accepts_matching_payment_hash(client, swap_data):
    """Legitimate swap with correct payment_hash accepted.
    REF: electrum test_reverse_swap_claims_to_external_output (happy path)"""
    swap = _make_swap(swap_data)
    client.verify_swap_response(
        swap,
        expected_preimage_hash=swap_data["preimage_hash"],
        expected_amount=100000,
    )


def test_rejects_mismatched_payment_hash(client, swap_data):
    """Invoice payment_hash differs from swap preimage hash.
    REF: electrum reverse_swap(): 'if lnaddr.paymenthash != payment_hash: raise'"""
    swap = _make_swap(swap_data)
    from unittest.mock import patch as mock_patch

    mock_decoded = mock_patch("boltz_client.boltz.bolt11_decode",
                              create=True,
                              return_value=type("D",(),{"payment_hash": swap_data["attacker_hash"],
                                                        "amount_msat": 99000000,
                                                        "expiry": 3600,
                                                        "date": 9999999999})())
    with mock_decoded:
        with pytest.raises(BoltzVerificationException):
            client.verify_swap_response(
                swap,
                expected_preimage_hash=swap_data["preimage_hash"],
            )


def test_rejects_low_onchain_amount(client, swap_data):
    """onchainAmount far below expected.
    REF: electrum test_reverse_swap_does_not_claim_underpaid_lockup_utxo"""
    swap = _make_swap(swap_data, onchainAmount=1000)
    with pytest.raises(BoltzVerificationException, match="too low"):
        client.verify_swap_response(swap, expected_amount=100000)


def test_rejects_locktime_too_close(client, swap_data):
    """Locktime within MIN_LOCKTIME_DELTA of current height.
    REF: electrum #10827 b6241d58: 'if locktime - height < MIN_LOCKTIME_DELTA: raise'"""
    swap = _make_swap(swap_data, timeoutBlockHeight=1000005)
    with pytest.raises(BoltzVerificationException, match="too close"):
        client.verify_swap_response(swap, current_blockheight=1000000)


def test_rejects_locktime_too_far(client, swap_data):
    """Locktime exceeds MAX_LOCKTIME_DELTA.
    REF: electrum request_normal_swap(): 'if locktime - height > MAX_LOCKTIME_DELTA: raise'"""
    swap = _make_swap(swap_data, timeoutBlockHeight=2000000)
    with pytest.raises(BoltzVerificationException, match="too far"):
        client.verify_swap_response(swap, current_blockheight=1000000)


def test_rejects_wrong_redeem_script_hash(client, swap_data):
    """redeemScript contains wrong preimage hash160.
    REF: electrum _check_swap_scriptcode(): 'if ripemd(payment_hash) != parsed_script[5][1]: raise'"""
    bad_script = _build_redeem_script(swap_data["attacker_hash"], swap_data["claim_pubkey_hex"])
    swap = _make_swap(swap_data, redeemScript=bad_script)
    with pytest.raises(BoltzVerificationException):
        client.verify_swap_response(
            swap,
            expected_preimage_hash=swap_data["preimage_hash"],
            expected_claim_pubkey=swap_data["claim_pubkey_hex"],
        )


def test_rejects_wrong_claim_pubkey_in_script(client, swap_data):
    """redeemScript contains wrong claim pubkey.
    REF: electrum _check_swap_scriptcode(): 'if claim_pubkey != parsed_script[7][1]: raise'"""
    attacker_cpk = (bytes([2]) + secrets.token_bytes(32)).hex()
    bad_script = _build_redeem_script(swap_data["preimage_hash"], attacker_cpk)
    swap = _make_swap(swap_data, redeemScript=bad_script)
    with pytest.raises(BoltzVerificationException, match="pubkey"):
        client.verify_swap_response(
            swap,
            expected_preimage_hash=swap_data["preimage_hash"],
            expected_claim_pubkey=swap_data["claim_pubkey_hex"],
        )


def test_rejects_tampered_script_opcode(client, swap_data):
    """First byte of redeemScript changed.
    REF: electrum match_script_against_template(): rejects scripts not matching template"""
    tampered = "ff" + swap_data["redeem_script"][2:]
    swap = _make_swap(swap_data, redeemScript=tampered)
    with pytest.raises(BoltzVerificationException, match="byte"):
        client.verify_swap_response(swap)


def test_rejects_short_script(client, swap_data):
    """redeemScript too short to be valid HTLC.
    REF: electrum match_script_against_template(): rejects malformed scripts"""
    swap = _make_swap(swap_data, redeemScript="00ff")
    with pytest.raises(BoltzVerificationException, match="length"):
        client.verify_swap_response(swap)


def test_no_checks_when_no_expected_values(client, swap_data):
    """Backward compatibility: no checks when no expected values provided.
    REF: clboss InvoicePayer — empty expected_payment_hash means skip check"""
    swap = _make_swap(swap_data)
    client.verify_swap_response(swap)
