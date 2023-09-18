# LNbits Extension for [Boltz](https://boltz.exchange)

Swap **IN** and **OUT** of the **lightning network** and remain in control of your bitcoin, at all times.

- [Boltz CLN](https://amboss.space/node/02d96eadea3d780104449aca5c93461ce67c1564e2e1d73225fa67dd3b997a6018) | [Boltz LND](https://amboss.space/node/026165850492521f4ac8abd9bd8088123446d126f648ca35e60f88177dc149ceb2)
- [Documentation](https://docs.boltz.exchange)
- [Discord](https://discord.com/invite/QBvZGcW)
- [X/Twitter](https://twitter.com/Boltzhq)

# Usage

This extension lets you swap in (onchain -> lightning), swap out (lightning -> onchain) and in the case of failure refund your bitcoin. Liquid swaps are currently not supported.

## Swap In (Onchain -> Lightning)

1. Click on the "Swap (IN)" button to open the following dialog, select a wallet, choose an amount within the min-max range and choose a onchain address for your refund in case the swap fails after you already sent onchain bitcoin.

---

## ![Create Swap](https://imgur.com/OyOh3Nm.png)

2. After you confirmed your inputs, the following dialog with a QR code for the onchain transaction, onchain- address and amount, will pop up.

---

## ![Pay Onchain TX](https://imgur.com/r2UhwCY.png)

3. After you sent the exact amount of onchain bitcoin to this address, Boltz will pay your invoice and the sats will appear in your wallet.

## Refund of Swap In (Onchain -> Lightning)

If a Swap In fails, you can refund your bitcoin after the timeout blockheight was reached. A swap can fail because Boltz, for instance, can't find a route to your lightning node or wallet. In case that happens, there is an info icon in the Swap (In) list which opens following dialog:

---

## ![Refund](https://imgur.com/pN81ltf.png)

When the timeout blockheight was reached you can either press refund and lnbits will do the refunding to the address you specified when creating the swap OR you can download the refund file so you can manually refund your onchain bitcoin via the [boltz.exchange website](https://boltz.exchange/refund). If you need help or have questions you can contact us in the LNbits Telegram or via the Boltz [Discord Server](https://discord.gg/d6EK85KK). In a recent update we added anl _automated check_; lnbits now checks every 15 minutes if it can refund your failed swap.

## Swap Out (Lightning -> Onchain)

1. Click on the "Swap (OUT)" button to open the following dialog, select a wallet, choose an amount within the min-max range and choose an onchain address to receive your funds on. Instant settlement: means that LNbits will create the onchain claim transaction if it sees Boltz's lockup transaction in the mempool, but it is not confirmed yet. For urgent swaps we advise to leave this enabled.

---

## ![Reverse Swap](https://imgur.com/UEAPpbs.png)

If this swap fails, no further action is required, the lightning payment just "bounces back".
