""" boltz_client CLI """

import asyncio
import json

# import sys
from typing import Optional

import click

from boltz_client.boltz import BoltzClient, BoltzConfig, SwapDirection

# disable tracebacks on exceptions
# sys.tracebacklimit = 0

config = BoltzConfig()

# use for manual testing
# config = BoltzConfig(
#     pairs=["BTC/BTC", "L-BTC/BTC"],
#     network="regtest",
#     network_liquid="elementsregtest",
#     api_url="http://localhost:9001",
#     mempool_url="http://localhost:8999/api/v1",
#     mempool_liquid_url="http://localhost:8998/api/v1",
# )


@click.group()
def command_group():
    """
    Python CLI of boltz-client-python, enjoy submarine swapping. :)
    Uses mempool.space for retrieving onchain data"""


@click.command()
@click.argument("payment_request", type=str)
@click.argument("pair", type=str, default="BTC/BTC")
def create_swap(payment_request: str, pair: str = "BTC/BTC"):
    """
    create a swap
    boltz will pay your invoice after you paid the onchain address

    SATS you want to swap, has to be the same as in PAYMENT_REQUEST
    PAYMENT_REQUEST with the same amount as specified in SATS
    """
    client = BoltzClient(config, pair)
    refund_privkey_wif, swap = client.create_swap(payment_request)

    click.echo()
    click.echo(f"boltz_id: {swap.id}")
    click.echo()
    click.echo(f"refund privkey in wif: {refund_privkey_wif}")
    click.echo(f"redeem_script_hex: {swap.redeemScript}")
    click.echo()
    click.echo(f"onchain address: {swap.address}")
    click.echo(f"expected amount: {swap.expectedAmount}")
    click.echo(f"bip21 address: {swap.bip21}")
    click.echo(f"timeout block height: {swap.timeoutBlockHeight}")
    if swap.blindingKey:
        click.echo(f"blinding key: {swap.blindingKey}")

    click.echo()
    click.echo("run this command if you need to refund:")
    click.echo("CHANGE YOUR_RECEIVEADDRESS to your onchain address!!!")
    click.echo(
        f"boltz refund-swap {swap.id} {refund_privkey_wif} {swap.address} YOUR_RECEIVEADDRESS "
        f"{swap.redeemScript} {swap.timeoutBlockHeight} {pair} {swap.blindingKey}"
    )


@click.command()
@click.argument("boltz_id", type=str)
@click.argument("privkey_wif", type=str)
@click.argument("lockup_address", type=str)
@click.argument("receive_address", type=str)
@click.argument("redeem_script_hex", type=str)
@click.argument("timeout_block_height", type=int)
@click.argument("pair", type=str, default="BTC/BTC")
@click.argument("blinding_key", type=str, default=None)
def refund_swap(
    boltz_id: str,
    privkey_wif: str,
    lockup_address: str,
    receive_address: str,
    redeem_script_hex: str,
    timeout_block_height: int,
    pair: str = "BTC/BTC",
    blinding_key: Optional[str] = None,
):
    """
    refund a swap
    """
    client = BoltzClient(config, pair)
    txid = asyncio.run(
        client.refund_swap(
            boltz_id=boltz_id,
            privkey_wif=privkey_wif,
            lockup_address=lockup_address,
            receive_address=receive_address,
            redeem_script_hex=redeem_script_hex,
            timeout_block_height=timeout_block_height,
            blinding_key=blinding_key,
        )
    )
    click.echo("swap refunded!")
    click.echo(f"TXID: {txid}")


@click.command()
@click.argument("sats", type=int)
@click.argument("pair", type=str, default="BTC/BTC")
@click.argument("direction", type=str, default="send")
def create_reverse_swap(sats: int, pair: str = "BTC/BTC", direction: str = "send"):
    """
    create a reverse swap
    """
    client = BoltzClient(config, pair)
    if direction == SwapDirection.receive:
        sats = client.add_reverse_swap_fees(sats)
    elif direction == SwapDirection.send:
        # don't do anything on reverse swap
        pass
    else:
        raise ValueError(
            f"direction must be '{SwapDirection.send}' or '{SwapDirection.receive}'"
        )
    claim_privkey_wif, preimage_hex, swap = client.create_reverse_swap(sats)

    click.echo("reverse swap created!")
    click.echo()
    click.echo(f"claim privkey in wif: {claim_privkey_wif}")
    click.echo(f"preimage hex: {preimage_hex}")
    click.echo(f"lockup_address: {swap.lockupAddress}")
    click.echo(f"redeem_script_hex: {swap.redeemScript}")
    if swap.blindingKey:
        click.echo(f"blinding key: {swap.blindingKey}")
    click.echo()
    click.echo(f"boltz_id: {swap.id}")
    click.echo()
    click.echo("invoice:")
    click.echo(swap.invoice)

    click.echo()
    click.echo("run this command after you see the lockup transaction:")
    click.echo("CHANGE YOUR_RECEIVEADDRESS to your onchain address!!!")
    zeroconf = "true"
    click.echo(
        f"boltz claim-reverse-swap {swap.id} {swap.lockupAddress} YOUR_RECEIVEADDRESS "
        f"{claim_privkey_wif} {preimage_hex} {swap.redeemScript} {pair} {zeroconf} {swap.blindingKey}"
    )


@click.command()
@click.argument("receive_address", type=str)
@click.argument("sats", type=int)
@click.argument("pair", type=str, default="BTC/BTC")
@click.argument("zeroconf", type=bool, default=True)
@click.argument("direction", type=str, default="send")
def create_reverse_swap_and_claim(
    receive_address: str,
    sats: int,
    pair: str = "BTC/BTC",
    zeroconf: bool = True,
    direction: str = "send",
):
    """
    create a reverse swap and claim
    """
    client = BoltzClient(config, pair)
    if direction == SwapDirection.receive:
        sats = client.add_reverse_swap_fees(sats)
    elif direction == SwapDirection.send:
        # don't do anything on reverse swap
        pass
    else:
        raise ValueError(
            f"direction must be '{SwapDirection.send}' or '{SwapDirection.receive}'"
        )

    claim_privkey_wif, preimage_hex, swap = client.create_reverse_swap(sats)

    click.echo("reverse swap created!")
    click.echo()
    click.echo(f"claim privkey in wif: {claim_privkey_wif}")
    click.echo(f"preimage hex: {preimage_hex}")
    click.echo(f"lockup_address: {swap.lockupAddress}")
    click.echo(f"redeem_script_hex: {swap.redeemScript}")
    if swap.blindingKey:
        click.echo(f"blinding key: {swap.blindingKey}")
    click.echo()
    click.echo(f"boltz_id: {swap.id}")
    click.echo()
    click.echo("invoice:")
    click.echo(swap.invoice)
    click.echo()
    click.echo("1. waiting until you paid the invoice...")
    click.echo("2. waiting for boltz to create the lockup transaction...")
    if not zeroconf:
        click.echo("3. waiting for lockup tx confirmation...")

    txid = asyncio.run(
        client.claim_reverse_swap(
            boltz_id=swap.id,
            lockup_address=swap.lockupAddress,
            receive_address=receive_address,
            privkey_wif=claim_privkey_wif,
            preimage_hex=preimage_hex,
            redeem_script_hex=swap.redeemScript,
            zeroconf=zeroconf,
            blinding_key=swap.blindingKey,
        )
    )

    click.echo("reverse swap claimed!")
    click.echo(f"TXID: {txid}")


@click.command()
@click.argument("boltz_id", type=str)
@click.argument("lockup_address", type=str)
@click.argument("receive_address", type=str)
@click.argument("privkey_wif", type=str)
@click.argument("preimage_hex", type=str)
@click.argument("redeem_script_hex", type=str)
@click.argument("pair", type=str, default="BTC/BTC")
@click.argument("zeroconf", type=bool, default=True)
@click.argument("blinding_key", type=str, default=None)
def claim_reverse_swap(
    boltz_id: str,
    lockup_address: str,
    receive_address: str,
    privkey_wif: str,
    preimage_hex: str,
    redeem_script_hex: str,
    pair: str = "BTC/BTC",
    zeroconf: bool = True,
    blinding_key: Optional[str] = None,
):
    """
    claims a reverse swap
    """
    client = BoltzClient(config, pair)

    txid = asyncio.run(
        client.claim_reverse_swap(
            boltz_id=boltz_id,
            lockup_address=lockup_address,
            receive_address=receive_address,
            privkey_wif=privkey_wif,
            preimage_hex=preimage_hex,
            redeem_script_hex=redeem_script_hex,
            zeroconf=zeroconf,
            blinding_key=blinding_key,
        )
    )

    click.echo("reverse swap claimed!")
    click.echo(f"TXID: {txid}")


@click.command()
@click.argument("swap_id", type=str)
def swap_status(swap_id):
    """
    get swap status
    retrieves the status of your boltz swap from the api

    ID is the id of your boltz swap
    """
    client = BoltzClient(config)
    data = client.swap_status(swap_id)
    click.echo(data)


@click.command()
@click.argument("amount", type=int)
def calculate_swap_send_amount(amount):
    """
    calculate the amount of the invoice you have to send to boltz
    to send the specified amount onchain
    """
    client = BoltzClient(config)
    click.echo(client.substract_swap_fees(amount))


@click.command()
def show_pairs():
    """
    show pairs of possible assets to swap
    """
    client = BoltzClient(config)
    data = client.get_pairs()
    click.echo(json.dumps(data))


@click.command()
def get_fees():
    """
    show mempool recommended fees
    """
    client = BoltzClient(config)
    fees = client.mempool.get_fees()
    click.echo(fees)


def main():
    """main function"""
    command_group.add_command(swap_status)
    command_group.add_command(show_pairs)
    command_group.add_command(get_fees)
    command_group.add_command(create_swap)
    command_group.add_command(refund_swap)
    command_group.add_command(create_reverse_swap)
    command_group.add_command(create_reverse_swap_and_claim)
    command_group.add_command(claim_reverse_swap)
    command_group.add_command(calculate_swap_send_amount)
    command_group()


if __name__ == "__main__":
    main()
