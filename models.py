from typing import Optional

from fastapi import Query
from pydantic import BaseModel


class BoltzSettings(BaseModel):
    boltz_network: str = "main"
    boltz_url: str = "https://boltz.exchange/api"
    boltz_mempool_space_url: str = "https://mempool.space/api/v1"
    boltz_mempool_space_url_ws: str = "wss://mempool.space/api/v1/ws"


class SubmarineSwap(BaseModel):
    id: str
    wallet: str
    amount: int
    direction: str
    feerate: bool
    feerate_value: Optional[int]
    payment_hash: str
    time: int
    status: str
    refund_privkey: str
    refund_address: str
    boltz_id: str
    expected_amount: int
    timeout_block_height: int
    address: str
    bip21: str
    redeem_script: str


class CreateSubmarineSwap(BaseModel):
    wallet: str = Query(...)
    refund_address: str = Query(...)
    amount: int = Query(...)
    direction: str = Query("receive")
    feerate: bool = Query(...)
    feerate_value: Optional[int] = Query(None)


class ReverseSubmarineSwap(BaseModel):
    id: str
    wallet: str
    amount: int
    direction: str
    feerate: bool
    feerate_value: Optional[int]
    onchain_address: str
    instant_settlement: bool
    time: int
    status: str
    boltz_id: str
    preimage: str
    claim_privkey: str
    lockup_address: str
    invoice: str
    onchain_amount: int
    timeout_block_height: int
    redeem_script: str


class CreateReverseSubmarineSwap(BaseModel):
    wallet: str = Query(...)
    amount: int = Query(...)
    direction: str = Query("send")
    instant_settlement: bool = Query(...)
    onchain_address: str = Query(...)
    feerate: bool = Query(...)
    feerate_value: Optional[int] = Query(None)


class AutoReverseSubmarineSwap(BaseModel):
    id: str
    wallet: str
    amount: int
    feerate_limit: Optional[int]
    balance: int
    onchain_address: str
    instant_settlement: bool
    time: int
    count: int


class CreateAutoReverseSubmarineSwap(BaseModel):
    wallet: str = Query(...)
    amount: int = Query(...)
    balance: int = Query(0)
    instant_settlement: bool = Query(...)
    onchain_address: str = Query(...)
    feerate_limit: Optional[int] = Query(None)
