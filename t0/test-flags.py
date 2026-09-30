"""Verify that pause is accepted only for equity rebalancing."""

import copy
import json
from pathlib import Path
import subprocess
import tomllib
import unittest


TOKEN_DIRECTORY = Path(__file__).resolve().parent


class EquityFlagValidation(unittest.TestCase):
    def test_pause_is_only_a_rebalancing_mode(self):
        with (TOKEN_DIRECTORY / "production.toml").open("rb") as token_file:
            production = tomllib.load(token_file)
        for field in ("trading", "rebalancing", "wrapped_equity_recovery"):
            for value in ("enabled", "disabled", "paused", "invalid"):
                with self.subTest(field=field, value=value):
                    candidate = copy.deepcopy(production)
                    candidate["chains"]["base"]["assets"]["equities"]["RKLB"][field] = value
                    result = subprocess.run(
                        ["jq", "-r", "-f", str(TOKEN_DIRECTORY / "check.jq")],
                        input=json.dumps(candidate),
                        text=True,
                        capture_output=True,
                        check=True,
                    )
                    allowed = value in ("enabled", "disabled") or (
                        field == "rebalancing" and value == "paused"
                    )
                    if allowed:
                        self.assertEqual(result.stdout, "")
                    else:
                        self.assertIn(field, result.stdout)


if __name__ == "__main__":
    unittest.main()
