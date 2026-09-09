# ST0x fixed spread v7

Offers a vault-share price DERIVED on-chain from the ST0x oracle's signed
**underlying** price and the wrapped-token vault's **live** NAV — no NAV
assertion, no revert on a NAV step.

The v5 protections carry over unchanged:

- **Trusted signer** — the signed context must come from the ST0x oracle signer.
- **Schema guard** — the frame must declare schema version 7; anything else reverts.
- **Pair binding** — the signed frame commits to this order's exact input/output
  tokens, so a frame for one pair can never be replayed against another.
- **Session gating** — fills only during an active market session (`rth`,
  `premarket`, `afterhours`), inside the session window the oracle stamped.
- **Frame expiry** — each signed frame carries an absolute deadline; fills after
  it revert.
- **Min/max price bounds** — deployer-set floor and ceiling as safety guards
  against catastrophic mispricings or encoding errors. In v7 the bounds apply to
  the DERIVED vault price, not the signed underlying price.

New in v7: **the price is derived, not gated**. The wrapped-token (wt) share you
trade is a share in an ERC4626 vault whose NAV can step between quote and fill —
for example when a dividend is deposited. v6 signed the vault price together with
the exact NAV it was computed against and reverted any fill whose live NAV had
moved. That exact-match gate is a denial-of-service surface (audit H03): a NAV
step is outside any taker's control, yet every step bricks otherwise-valid signed
frames.

v7 removes the gate. The oracle signs only the **underlying** (stock) price at
slot 1 — the same directional, spread-included, ratio-unit orientation v5/v6 used
for the vault-share price, but pricing the ERC4626 underlying asset rather than
the share. The NAV ratio is never signed. At settlement the strategy reads the
vault's **live** conversion rate on-chain and multiplies:

```
vault_price = underlying_price × (vault conversion for this direction)
```

This is atomic — the ratio used is always the live one, so there is nothing to
straddle and nothing to revert on. A dividend deposit that steps the NAV between
quote and fill simply moves the derived price to the new, correct value; the fill
still executes.

The conversion the strategy applies depends on which side of the pair is the
vault, exactly as the two deployments already distinguish:

- **Sell shares** — you sell the wt vault share (e.g. wtCOIN) for the quote token
  (e.g. USDC). The vault is the **output** token, so the order ratio is quote per
  share and the derived price is `underlying × erc4626-convert-to-assets(vault, 1
  share)` (assets per share).
- **Buy shares** — you sell the quote token to receive the wt vault share. The
  vault is the **input** token, so the order ratio is shares per quote and the
  derived price is `underlying × erc4626-convert-to-shares(vault, 1 asset)`
  (shares per asset).

Each direction reads the vault's own live conversion for that direction, so the
derived ratio is always the genuine on-chain rate — not a rounded reciprocal of
the other side.

The oracle fails closed on a zero underlying price (a producer that carried no
underlying rate), so slot 1 is always a real price; a real vault's live
conversion is never zero, so the derived price is always positive. The strategy
only deploys once the oracle server serves `/context/v7`, and only on pairs whose
wt side is a real ERC4626 vault — for pairs without a vault side, use v5.

Two deployments, because the strategy must know which side of the pair is the
vault:

- **Sell shares** — the vault is the output token.
- **Buy shares** — the vault is the input token.

Uses the ERC4626 words subparser
([rainlanguage/rain.erc4626.words](https://github.com/rainlanguage/rain.erc4626.words))
on Base: `0xd69dC3d58a7C875117f9c7cecF4F1A7f3CA47254`.
