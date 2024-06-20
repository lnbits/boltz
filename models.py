from typing import Optional, Union

from fastapi import Query
from pydantic import BaseModel


class BoltzSettings(BaseModel):
    boltz_network: str = "main"
    boltz_network_liquid: str = "liquidv1"
    boltz_url: str = "https://boltz.exchange/api"


class SubmarineSwap(BaseModel):
    id: str
    wallet: str
    asset: str
    amount: int
    direction: str
    feerate: bool
    feerate_value: Optional[int]
    payment_hash: str
    time: Union[str, float]
    status: str
    refund_privkey: str
    refund_address: str
    boltz_id: str
    expected_amount: int
    timeout_block_height: int
    address: str
    bip21: str
    redeem_script: str
    blinding_key: Optional[str]


class CreateSubmarineSwap(BaseModel):
    wallet: str = Query(...)
    asset: str = Query("BTC/BTC")
    refund_address: str = Query(...)
    amount: int = Query(...)
    direction: str = Query("receive")
    feerate: bool = Query(...)
    feerate_value: Optional[int] = Query(None)


class ReverseSubmarineSwap(BaseModel):
    id: str
    wallet: str
    asset: str
    amount: int
    direction: str
    feerate: bool
    feerate_value: Optional[int]
    onchain_address: str
    instant_settlement: bool
    time: Union[str, float]
    status: str
    boltz_id: str
    preimage: str
    claim_privkey: str
    lockup_address: str
    invoice: str
    onchain_amount: int
    timeout_block_height: int
    redeem_script: str
    blinding_key: Optional[str]


class CreateReverseSubmarineSwap(BaseModel):
    wallet: str = Query(...)
    asset: str = Query("BTC/BTC")
    amount: int = Query(...)
    direction: str = Query("send")
    instant_settlement: bool = Query(...)
    onchain_address: str = Query(...)
    feerate: bool = Query(...)
    feerate_value: Optional[int] = Query(None)


class AutoReverseSubmarineSwap(BaseModel):
    id: str
    wallet: str
    asset: str
    amount: int
    feerate_limit: Optional[int]
    balance: int
    onchain_address: str
    instant_settlement: bool
    time: Union[str, float]
    count: int


class CreateAutoReverseSubmarineSwap(BaseModel):
    wallet: str = Query(...)
    asset: str = Query("BTC/BTC")
    amount: int = Query(...)
    balance: int = Query(0)
    instant_settlement: bool = Query(...)
    onchain_address: str = Query(...)
    feerate_limit: Optional[int] = Query(None)
