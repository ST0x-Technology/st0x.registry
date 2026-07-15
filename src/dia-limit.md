# DIA limit

Offers the **minimum** of:

1. **DIA market quote** — on-chain DIA price × `baseline-multiplier` (same pattern as [DIA fixed-spread](https://raw.githubusercontent.com/rainlanguage/rain.strategies/refs/heads/dia-v5-0.1/src/fixed-spread.rain))
2. **Fixed limit** — a classic limit IO ratio (inverted when the vault output token does not match `fixed-io-output-token`)

Use this when you want the book to track DIA with a spread, but never offer a worse price than your hard limit.

Uses DiaWords on Base: `0xb4bf51998aB1f1b733C9df4a06e131182dBDf447`.
