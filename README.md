<a href="https://lnbits.com" target="_blank" rel="noopener noreferrer">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://i.imgur.com/QE6SIrs.png">
    <img src="https://i.imgur.com/fyKPgVT.png" alt="LNbits" style="width:280px">
  </picture>
</a>

[![License: MIT](https://img.shields.io/badge/License-MIT-success?logo=open-source-initiative&logoColor=white)](./LICENSE)
[![Built for LNbits](https://img.shields.io/badge/Built%20for-LNbits-4D4DFF?logo=lightning&logoColor=white)](https://github.com/lnbits/lnbits)

# LNbits Extension for [Boltz](https://boltz.exchange)

Swap **IN** and **OUT** of the **Lightning Network** while remaining in full control of your bitcoin at all times.

- [Boltz CLN](https://amboss.space/node/02d96eadea3d780104449aca5c93461ce67c1564e2e1d73225fa67dd3b997a6018) | [Boltz LND](https://amboss.space/node/026165850492521f4ac8abd9bd8088123446d126f648ca35e60f88177dc149ceb2)
- [Documentation](https://docs.boltz.exchange)
- [Discord](https://discord.com/invite/QBvZGcW)
- [X/Twitter](https://twitter.com/Boltzhq)

# Usage

This extension lets you swap in (chain -> lightning), swap out (lightning -> chain) and, in the case of failure, refund your bitcoin. Supported chains are [liquid](https://liquid.net/) and the [bitcoin mainchain](https://bitcoin.org/).

## Swap In (Onchain -> Lightning)

1. Click on the "Swap (IN)" button to open the following dialog, select a wallet, choose an amount within the min-max range and choose an onchain address for your refund in case the swap fails after you already sent onchain bitcoin.

---

## ![Create Swap](https://imgur.com/OyOh3Nm.png)

2. After you confirmed, the following dialog with a QR code for the onchain transaction, onchain- address and amount, will pop up.

---

## ![Pay Onchain TX](https://imgur.com/r2UhwCY.png)

3. After you sent the exact amount of onchain bitcoin to this address, Boltz will pay your lightning invoice and the sats will appear in your wallet.

### Refund of Swap In (Onchain -> Lightning)

If a Swap In fails, you can refund your bitcoin after the timeout blockheight was reached. A swap can fail because Boltz, for instance, can't find a route to your lightning node or wallet. In case that happens, there is an info icon in the Swap (In) list which opens following dialog:

---

## ![Refund](https://imgur.com/pN81ltf.png)

When the timeout blockheight was reached you can either press refund and lnbits will do the refunding to the address you specified when creating the swap OR you can download the refund file so you can manually refund your onchain bitcoin to a different address via the [boltz.exchange website](https://boltz.exchange/refund). If you need help or have questions you can contact us in the [LNbits Telegram](https://t.me/lnbits) or the Boltz Team on [Discord](https://discord.gg/d6EK85KK). In a recent update we added anl _automated check_; lnbits now checks every 15 minutes if it can refund your failed swap.

## Swap Out (Lightning -> Onchain)

1. Click on the "Swap (OUT)" button to open the following dialog, select a wallet, choose an amount within the min-max range and choose an onchain address to receive your funds on. Instant settlement: means that LNbits will create the onchain claim transaction if it sees Boltz's lockup transaction in the mempool, but it is not confirmed yet. For urgent swaps we advise to leave this enabled.

---

## ![Reverse Swap](https://imgur.com/UEAPpbs.png)

If a Swap Out fails, no further action is required, the lightning payment just "bounces back".

# Development

## manual testcases for boltz startup checks

A. normal swaps

1. test: create -> kill -> start -> startup invoice listeners -> pay onchain funds -> should complete
2. test: create -> kill -> pay onchain funds -> mine block -> start -> startup check -> should complete
3. test: create -> kill -> mine blocks and hit timeout -> start -> should go timeout/failed
4. test: create -> kill -> pay to less onchain funds -> mine blocks hit timeout -> start lnbits -> should be refunded

B. reverse swaps

1. test: create instant -> kill -> boltz does lockup -> not confirmed -> start lnbits -> should claim/complete
2. test: create -> kill -> boltz does lockup -> not confirmed -> start lnbits -> mine blocks -> should claim/complete
3. test: create -> kill -> boltz does lockup -> confirmed -> start lnbits -> should claim/complete

## Powered by LNbits

[LNbits](https://lnbits.com) is a free and open-source lightning accounts system.

[![Visit LNbits Shop](https://img.shields.io/badge/Visit-LNbits%20Shop-7C3AED?logo=shopping-cart&logoColor=white&labelColor=5B21B6)](https://shop.lnbits.com/)
[![Try myLNbits SaaS](https://img.shields.io/badge/Try-myLNbits%20SaaS-2563EB?logo=lightning&logoColor=white&labelColor=1E40AF)](https://my.lnbits.com/login)
