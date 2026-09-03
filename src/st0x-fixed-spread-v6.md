# ST0x fixed spread v6

Offers the ST0x oracle's signed baseline price, with every v5 protection plus a
settlement-time check of the wrapped-token vault's NAV.

The v5 protections carry over unchanged:

- **Trusted signer** — the signed context must come from the ST0x oracle signer.
- **Schema guard** — the frame must declare schema version 6; anything else reverts.
- **Pair binding** — the signed frame commits to this order's exact input/output
  tokens, so a frame for one pair can never be replayed against another.
- **Session gating** — fills only during an active market session (`rth`,
  `premarket`, `afterhours`), inside the session window the oracle stamped.
- **Frame expiry** — each signed frame carries an absolute deadline; fills after
  it revert.
- **Min/max price bounds** — deployer-set floor and ceiling as safety guards
  against catastrophic mispricings or encoding errors.

New in v6: **vault NAV assertion**. The wrapped-token (wt) share you trade is a
share in an ERC4626 vault whose NAV can step between quote and fill — for
example when a dividend is deposited. A price computed against one NAV but
settled against another is stale in a way no timestamp check can see. The v6
oracle signs the vault NAV ratio the pricing model priced the quote against —
the lossless Rain Float packing, at 18 decimals, of the vault's raw
`convertToAssets(1e18)` — and at settlement the strategy reads the vault's live
value via `erc4626-convert-to-assets` and requires exact equality. Because both
sides are lossless packings of the same raw 18-decimal value, value equality is
equivalent to bit-for-bit equality on the raw ratio. Any fill that straddles a
NAV step reverts instead of settling at a mispriced rate.

The assertion is unconditional: the vault read and the equality check run on
every fill. A real vault's live `convertToAssets` is never zero, so a signed
ratio of zero — an upstream that has stopped publishing ratios — fails the
equality and the fill reverts. The strategy halts rather than settling
unprotected; that is intended. It therefore only deploys once the oracle
server serves `/context/v6` **and** the pricing model publishes real ratios,
and only on pairs whose wt side is a real ERC4626 vault — for pairs without a
vault side, use v5.

Two deployments, because the strategy must know which side of the pair is the
vault:

- **Sell shares** — you sell the wt vault share (e.g. wtCOIN) for the quote
  token (e.g. USDC). The vault is the output token.
- **Buy shares** — you sell the quote token to receive the wt vault share. The
  vault is the input token.

Uses the ERC4626 words subparser
([rainlanguage/rain.erc4626.words](https://github.com/rainlanguage/rain.erc4626.words))
on Base: `0xd69dC3d58a7C875117f9c7cecF4F1A7f3CA47254`.
