"""Tests for underlying_naming.py.

Run with `python3 -m unittest discover -s scripts -p 'test_*.py'`. Set
ST0X_DEPLOY_DIR to a st0x.deploy checkout at DEPLOY_COMMIT to run offline.
"""

import copy
import os
import unittest

import underlying_naming as naming

BASE = 8453
ETHEREUM = 1
HYPEREVM = 999
ROBINHOOD = 4663


def load_lists():
    lists = {}
    for chain_id, (filename, _) in naming.CHAINS.items():
        data, _, _ = naming.read_list(naming.TOKEN_LISTS / filename)
        lists[chain_id] = data
    return lists


def find(data, symbol):
    return next(token for token in data["tokens"] if token["symbol"] == symbol)


CONFIG_ROW = """
        configs = new TokenConfig[](1);
        configs[0] = TokenConfig({
            underlying: "MSTR",
            name: "MicroStrategy Incorporated ST0x",
            symbol: "tMSTR",
            region: Region.US,
            underlyingName: %s
        });
"""


class ParserFailsClosedTest(unittest.TestCase):
    def test_plain_row_parses(self):
        self.assertEqual(naming.parse_configs(CONFIG_ROW % '"Strategy Inc"'), {"MSTR": "Strategy Inc"})

    def test_escape_sequence_fails(self):
        with self.assertRaises(naming.CheckError):
            naming.parse_configs(CONFIG_ROW % 'unicode"Mo\\u00ebt"')

    def test_concatenated_literal_fails(self):
        with self.assertRaises(naming.CheckError):
            naming.parse_configs(CONFIG_ROW % '"Strategy " "Inc"')

    def test_non_literal_name_fails(self):
        with self.assertRaises(naming.CheckError):
            naming.parse_configs(CONFIG_ROW % "NAME")

    def test_row_count_mismatch_fails(self):
        with self.assertRaises(naming.CheckError):
            naming.parse_configs((CONFIG_ROW % '"Strategy Inc"').replace("[](1)", "[](2)"))

    def test_unresolvable_address_fails(self):
        with self.assertRaises(naming.CheckError):
            naming.resolve_address("UNKNOWN_WRAPPED_TOKEN_VAULT", {})

    def test_malformed_names(self):
        for name in ["", "Tesla Inc ST0x", "Tesla Inc (ST0x)", 'Tesla "Inc"', "Tesla\nInc"]:
            self.assertFalse(naming.name_well_formed(name), name)
        self.assertTrue(naming.name_well_formed("McDonald's Corporation"))


class SourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.configs, cls.instances = naming.load_source(os.environ.get("ST0X_DEPLOY_DIR"))

    def errors(self, chain_id, data):
        return naming.check_list(chain_id, data, self.configs, self.instances)[0]


class ParseTest(SourceTest):
    def test_renamed_issuers_use_the_current_name(self):
        self.assertEqual(self.configs["MSTR"], "Strategy Inc")
        self.assertEqual(self.configs["RKLB"], "Rocket Lab Corporation")

    def test_unicode_literal_is_decoded(self):
        self.assertEqual(self.configs["MC"], "LVMH Moët Hennessy Louis Vuitton SE")

    def test_base_constants_resolve_to_addresses(self):
        tsla = self.instances[BASE]["0x219a8d384a10bf19b9f24cb5cc53f79dd0e5a03d"]
        self.assertEqual(tsla, ("TSLA", "0x4e169cd2ab4f82640a8c65c68fed55863866fdb0"))

    def test_every_chain_table_is_read(self):
        for chain_id in naming.CHAINS:
            self.assertGreaterEqual(len(self.instances[chain_id]), 56, chain_id)


class ListsTest(SourceTest):
    def setUp(self):
        self.lists = load_lists()

    def test_committed_lists_match_the_source(self):
        for chain_id, data in self.lists.items():
            self.assertEqual(self.errors(chain_id, data), [], chain_id)

    def test_spot_values(self):
        expected = {
            (BASE, "wtTSLA"): ("TSLAs", "Tesla Inc (ST0x)"),
            (BASE, "wtMSTR"): ("MSTRs", "Strategy Inc (ST0x)"),
            (ETHEREUM, "wtRKLB"): ("RKLBs", "Rocket Lab Corporation (ST0x)"),
            (HYPEREVM, "wtGOOGL"): ("GOOGLs", "Alphabet Inc. Class A (ST0x)"),
            (ROBINHOOD, "wtDIS"): ("DISs", "The Walt Disney Company (ST0x)"),
            (BASE, "wtMC"): ("MCs", "LVMH Moët Hennessy Louis Vuitton SE (ST0x)"),
        }
        for (chain_id, symbol), values in expected.items():
            extensions = find(self.lists[chain_id], symbol)["extensions"]
            self.assertEqual((extensions["nextSymbol"], extensions["nextName"]), values)

    def test_current_symbol_and_name_are_kept(self):
        token = find(self.lists[BASE], "wtTSLA")
        self.assertEqual(token["name"], "Wrapped Tesla Inc ST0x")
        self.assertEqual(token["address"], "0x219A8d384a10BF19b9f24cB5cC53F79Dd0e5A03D")

    def test_every_wrapper_in_the_deploy_table_is_covered(self):
        for chain_id, data in self.lists.items():
            for token in data["tokens"]:
                pending = (chain_id, token["symbol"][2:]) in naming.NOT_IN_DEPLOY_TABLE
                self.assertEqual("nextSymbol" in token["extensions"], not pending, token["symbol"])
                self.assertEqual("nextName" in token["extensions"], not pending, token["symbol"])

    def test_token_map_mirrors_tokens(self):
        data = self.lists[BASE]
        self.assertEqual(data["tokenMap"]["8453_" + find(data, "wtTSLA")["address"]], find(data, "wtTSLA"))


class MutationTest(SourceTest):
    def setUp(self):
        self.lists = load_lists()

    def assertRejected(self, chain_id, data):
        self.assertNotEqual(self.errors(chain_id, data), [])

    def test_swapped_symbols_fail(self):
        data = copy.deepcopy(self.lists[BASE])
        tsla = find(data, "wtTSLA")["extensions"]
        nvda = find(data, "wtNVDA")["extensions"]
        tsla["nextSymbol"], nvda["nextSymbol"] = nvda["nextSymbol"], tsla["nextSymbol"]
        self.assertRejected(BASE, data)

    def test_initialized_name_instead_of_underlying_name_fails(self):
        data = copy.deepcopy(self.lists[BASE])
        find(data, "wtMSTR")["extensions"]["nextName"] = "MicroStrategy Incorporated (ST0x)"
        self.assertRejected(BASE, data)

    def test_missing_suffix_fails(self):
        data = copy.deepcopy(self.lists[ETHEREUM])
        find(data, "wtRKLB")["extensions"]["nextName"] = "Rocket Lab Corporation"
        self.assertRejected(ETHEREUM, data)

    def test_missing_extension_fails(self):
        data = copy.deepcopy(self.lists[HYPEREVM])
        del find(data, "wtAAPL")["extensions"]["nextSymbol"]
        self.assertRejected(HYPEREVM, data)

    def test_replacing_symbol_fails(self):
        data = copy.deepcopy(self.lists[ROBINHOOD])
        find(data, "wtTSLA")["symbol"] = "TSLAs"
        self.assertRejected(ROBINHOOD, data)

    def test_symbol_on_the_wrong_address_fails(self):
        data = copy.deepcopy(self.lists[BASE])
        tsla = find(data, "wtTSLA")
        nvda = find(data, "wtNVDA")
        tsla["address"], nvda["address"] = nvda["address"], tsla["address"]
        self.assertRejected(BASE, data)

    def test_wrong_unwrapped_address_fails(self):
        data = copy.deepcopy(self.lists[BASE])
        find(data, "wtTSLA")["extensions"]["unwrappedAddress"] = find(data, "wtNVDA")["extensions"]["unwrappedAddress"]
        self.assertRejected(BASE, data)

    def test_pending_token_with_extensions_fails(self):
        data = copy.deepcopy(self.lists[BASE])
        find(data, "wtIFX")["extensions"]["nextSymbol"] = "IFXs"
        self.assertRejected(BASE, data)

    def test_unknown_wrapper_fails(self):
        data = copy.deepcopy(self.lists[ETHEREUM])
        find(data, "wtTSLA")["address"] = "0x" + "11" * 20
        self.assertRejected(ETHEREUM, data)

    def test_stale_token_map_fails(self):
        data = copy.deepcopy(self.lists[BASE])
        key = "8453_" + find(data, "wtTSLA")["address"]
        data["tokenMap"][key]["extensions"]["nextName"] = "Tesla (ST0x)"
        self.assertRejected(BASE, data)

    def test_pending_token_entering_the_table_fails(self):
        instances = copy.deepcopy(self.instances)
        ifx = find(self.lists[BASE], "wtIFX")
        instances[BASE][ifx["address"].lower()] = ("IFX", ifx["extensions"]["unwrappedAddress"].lower())
        errors = naming.check_list(BASE, self.lists[BASE], self.configs, instances)[0]
        self.assertTrue(any("NOT_IN_DEPLOY_TABLE" in error for error in errors), errors)

    def test_pending_token_entering_the_table_at_another_address_fails(self):
        instances = copy.deepcopy(self.instances)
        instances[BASE]["0x" + "22" * 20] = ("SOI", "0x" + "33" * 20)
        errors = naming.check_list(BASE, self.lists[BASE], self.configs, instances)[0]
        self.assertTrue(any("NOT_IN_DEPLOY_TABLE" in error for error in errors), errors)

    def test_write_is_idempotent(self):
        for chain_id, data in self.lists.items():
            rewritten = copy.deepcopy(data)
            naming.apply_list(chain_id, rewritten, self.configs, self.instances)
            self.assertEqual(rewritten, data, chain_id)


if __name__ == "__main__":
    unittest.main()
