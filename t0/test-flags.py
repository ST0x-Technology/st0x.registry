"""Verify check.jq's rules for a listing's trading, rebalancing and recovery flags."""

import copy
import json
from pathlib import Path
import subprocess
import tomllib
import unittest


TOKEN_DIRECTORY = Path(__file__).resolve().parent
FLAGS = ("trading", "rebalancing", "wrapped_equity_recovery")


def load_production():
    with (TOKEN_DIRECTORY / "production.toml").open("rb") as token_file:
        return tomllib.load(token_file)


def liquidity_listing(token_config):
    """The first listing with a liquidity row, so no case depends on one symbol."""
    for chain, chain_config in token_config["chains"].items():
        for symbol, listing in chain_config.get("assets", {}).get("equities", {}).items():
            if all(flag in listing for flag in FLAGS):
                return chain, symbol
    raise AssertionError("production.toml has no listing with a liquidity row")


def check(token_config):
    result = subprocess.run(
        ["jq", "-r", "-f", str(TOKEN_DIRECTORY / "check.jq")],
        input=json.dumps(token_config),
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    return result.stdout


class EquityFlagValidation(unittest.TestCase):
    def setUp(self):
        self.production = load_production()
        self.chain, self.symbol = liquidity_listing(self.production)
        self.where = f"{self.chain} {self.symbol}"

    def problems_with(self, **flags):
        """check.jq's output with every flag of the listing set, defaults first."""
        candidate = copy.deepcopy(self.production)
        listing = candidate["chains"][self.chain]["assets"]["equities"][self.symbol]
        listing.update(
            {"trading": "disabled", "rebalancing": "disabled", "wrapped_equity_recovery": "enabled"}
        )
        listing.update(flags)
        return check(candidate)

    def test_trading_and_recovery_take_enabled_or_disabled(self):
        refusal = f"{self.where}: trading/wrapped_equity_recovery must each be enabled or disabled\n"
        for field in ("trading", "wrapped_equity_recovery"):
            for value, expected in (
                ("enabled", ""),
                ("disabled", ""),
                ("paused", refusal),
                ("invalid", refusal),
            ):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.problems_with(**{field: value}), expected)

    def test_rebalancing_also_takes_paused(self):
        for value, expected in (
            ("enabled", ""),
            ("disabled", ""),
            ("paused", ""),
            ("invalid", f"{self.where}: rebalancing must be enabled, paused or disabled\n"),
        ):
            with self.subTest(value=value):
                self.assertEqual(self.problems_with(rebalancing=value), expected)

    def test_rebalancing_needs_recovery(self):
        refusal = f"{self.where}: rebalancing needs wrapped_equity_recovery enabled\n"
        for rebalancing, expected in (
            ("enabled", refusal),
            ("paused", refusal),
            ("disabled", ""),
        ):
            with self.subTest(rebalancing=rebalancing):
                self.assertEqual(
                    self.problems_with(rebalancing=rebalancing, wrapped_equity_recovery="disabled"),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
