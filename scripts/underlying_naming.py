#!/usr/bin/env python3
"""Sync and check the post-rename symbol and name of every ST0x wrapper.

The st0x.deploy rename sets the underlying ticker and name on every
production vault. After that timelock batch runs on a chain, each wrapped
token vault reports `symbol() == <TICKER> + "s"` and
`name() == <underlyingName> + " (ST0x)"`. The addresses do not change.

Until then the chain still reports `wt<TICKER>`, so the lists keep `symbol`
and `name` as the chain reports them today and carry the new values in two
extensions:

    extensions.nextSymbol  <TICKER>s
    extensions.nextName    <underlyingName> (ST0x)

Both values come from st0x.deploy at DEPLOY_COMMIT: the ticker and
underlying name from `LibProdTokenConfig.sol`, joined to each list entry by
chain id and wrapped token vault address through `LibTokenInvariants.sol`.

    underlying_naming.py write   rewrite the token lists from the source
    underlying_naming.py check   fail if a token list disagrees with it

Pass --deploy-dir to read a local st0x.deploy checkout instead of fetching
the pinned commit from GitHub. The checkout must be at DEPLOY_COMMIT.
"""

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

DEPLOY_REPO = "S01-Issuer/st0x.deploy"
# st0x.deploy main after #473 and #488, the PRs that define the rename.
DEPLOY_COMMIT = "970abea464764e2ce1adaabcb1b8c308ff9694e9"
CONFIG_PATH = "src/lib/LibProdTokenConfig.sol"
INVARIANTS_PATH = "src/lib/LibTokenInvariants.sol"

REPO_ROOT = Path(__file__).resolve().parent.parent
TOKEN_LISTS = REPO_ROOT / "token-lists"

# Chain id -> (token list file, LibTokenInvariants table function).
CHAINS = {
    8453: ("base.json", "productionTokensBase"),
    1: ("ethereum.json", "productionTokensEthereum"),
    999: ("hyperevm.json", "productionTokensHyperEvm"),
    4663: ("robinhood.json", "productionTokensRobinhood"),
}

# Listed wrappers that the pinned st0x.deploy table does not carry yet, so
# the pinned source has no underlying name for them and they get no new
# extensions. The rename batch covers a fixed 56 rows; a later row gets its
# own `setUnderlying` operation. When the pin moves to a commit that has
# their rows, `check` fails until they are removed here and the lists are
# rewritten.
NOT_IN_DEPLOY_TABLE = {
    (8453, "IFX"),
    (8453, "SOI"),
    (8453, "TPRO"),
    (4663, "IFX"),
    (4663, "SOI"),
    (4663, "TPRO"),
}

NEXT_SYMBOL = "nextSymbol"
NEXT_NAME = "nextName"
WRAPPED_PREFIX = "wt"
TICKER_RE = re.compile(r"^[A-Z0-9.]{1,16}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class CheckError(Exception):
    pass


def fetch_source(path, deploy_dir):
    if deploy_dir is not None:
        return (Path(deploy_dir) / path).read_text(encoding="utf-8")
    url = f"https://raw.githubusercontent.com/{DEPLOY_REPO}/{DEPLOY_COMMIT}/{path}"
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read().decode("utf-8")


def struct_bodies(source, struct):
    """Yield the field text of every `<struct>({ ... })` literal."""
    return re.findall(struct + r"\(\{(.*?)\}\)", source, re.S)


def string_fields(body):
    """Return the plain string literal fields of a struct literal.

    A field whose literal is followed by anything but `,` or the end of the
    literal (such as adjacent-literal concatenation) is left out, so the
    caller fails on the missing field. An escape sequence is refused rather
    than decoded.
    """
    fields = dict(re.findall(r'(\w+):\s*(?:unicode)?"([^"]*)"(?=\s*(?:,|\Z))', body))
    for key, value in fields.items():
        if "\\" in value:
            raise CheckError(f"escape sequence in {key}: {value!r}")
    return fields


def name_well_formed(name):
    """Mirror `LibUnderlyingNamingRollout.nameWellFormed` in st0x.deploy."""
    return (
        name != ""
        and not any(ord(c) < 0x20 or ord(c) == 0x7F or c in '"\\' for c in name)
        and not name.endswith("ST0x")
        and not name.endswith("(ST0x)")
    )


def declared_length(source, struct):
    """Return N from the single `new <struct>[](N)` in source."""
    lengths = re.findall(r"new " + struct + r"\[\]\((\d+)\)", source)
    if len(lengths) != 1:
        raise CheckError(f"expected one new {struct}[](N), found {lengths}")
    return int(lengths[0])


def parse_configs(source):
    """Return {ticker: underlyingName} from LibProdTokenConfig.sol."""
    configs = {}
    for body in struct_bodies(source, r"TokenConfig"):
        fields = string_fields(body)
        ticker = fields.get("underlying")
        underlying_name = fields.get("underlyingName")
        if ticker is None or underlying_name is None:
            raise CheckError(f"TokenConfig row without underlying or underlyingName: {body!r}")
        if ticker in configs:
            raise CheckError(f"duplicate TokenConfig row for {ticker}")
        configs[ticker] = underlying_name
    if len(configs) != declared_length(source, "TokenConfig"):
        raise CheckError(f"parsed {len(configs)} TokenConfig rows, the table declares another length")
    return configs


def function_body(source, name):
    start = source.find(f"function {name}(")
    if start < 0:
        raise CheckError(f"function {name} not found")
    end = source.find("\n    function ", start + 1)
    return source[start : end if end >= 0 else len(source)]


def resolve_address(value, constants):
    value = value.strip()
    match = re.fullmatch(r"address\((0x[0-9a-fA-F]{40})\)", value)
    if match:
        return match.group(1)
    if ADDRESS_RE.fullmatch(value):
        return value
    if value in constants:
        return constants[value]
    raise CheckError(f"cannot resolve address {value!r}")


def parse_instances(source):
    """Return {chainId: {wrapped address (lower): (ticker, receipt vault)}}."""
    constants = dict(
        re.findall(r"address internal constant (\w+) = address\((0x[0-9a-fA-F]{40})\);", source)
    )
    tables = {}
    for chain_id, (_, function) in CHAINS.items():
        table = {}
        body_source = function_body(source, function)
        for body in struct_bodies(body_source, r"TokenInstance"):
            fields = dict(re.findall(r"(\w+):\s*([^,\n]+?)\s*(?:,|$)", body.strip(), re.M))
            ticker = string_fields(body).get("underlying")
            if ticker is None or "wrappedTokenVault" not in fields or "receiptVault" not in fields:
                raise CheckError(f"chain {chain_id}: unreadable TokenInstance row: {body!r}")
            wrapped = resolve_address(fields["wrappedTokenVault"], constants).lower()
            receipt_vault = resolve_address(fields["receiptVault"], constants).lower()
            if wrapped in table:
                raise CheckError(f"chain {chain_id}: duplicate wrapped vault {wrapped}")
            table[wrapped] = (ticker, receipt_vault)
        if len(table) != declared_length(body_source, "TokenInstance"):
            raise CheckError(f"chain {chain_id}: parsed {len(table)} rows of {function}, it declares another length")
        tables[chain_id] = table
    return tables


def load_source(deploy_dir):
    configs = parse_configs(fetch_source(CONFIG_PATH, deploy_dir))
    instances = parse_instances(fetch_source(INVARIANTS_PATH, deploy_dir))
    return configs, instances


def expected_extensions(chain_id, token, configs, instances):
    """Return (nextSymbol, nextName) for a list entry, or None if not renamed.

    Raises CheckError when the entry and the deploy tables disagree.
    """
    symbol = token["symbol"]
    # These lists hold only wrappers; quote tokens live in the *-quotes.json
    # lists. An entry without the prefix fails rather than being skipped.
    if not symbol.startswith(WRAPPED_PREFIX):
        raise CheckError(f"chain {chain_id}: {symbol} has no {WRAPPED_PREFIX} prefix")
    listed_ticker = symbol[len(WRAPPED_PREFIX) :]
    instance = instances[chain_id].get(token["address"].lower())

    if instance is None:
        if (chain_id, listed_ticker) not in NOT_IN_DEPLOY_TABLE:
            raise CheckError(
                f"chain {chain_id}: {symbol} {token['address']} is not in the "
                f"st0x.deploy token table at {DEPLOY_COMMIT[:8]}"
            )
        return None

    ticker, receipt_vault = instance
    if (chain_id, ticker) in NOT_IN_DEPLOY_TABLE:
        raise CheckError(
            f"chain {chain_id}: {ticker} is in the st0x.deploy token table now; "
            "remove it from NOT_IN_DEPLOY_TABLE and run write"
        )
    if ticker != listed_ticker:
        raise CheckError(
            f"chain {chain_id}: {token['address']} is {symbol} in the list but "
            f"{ticker} in st0x.deploy"
        )
    unwrapped = token.get("extensions", {}).get("unwrappedAddress", "")
    if unwrapped.lower() != receipt_vault:
        raise CheckError(
            f"chain {chain_id}: {symbol} unwrappedAddress {unwrapped} is not the "
            f"st0x.deploy receipt vault {receipt_vault}"
        )
    if ticker not in configs:
        raise CheckError(f"chain {chain_id}: {ticker} has no LibProdTokenConfig row")
    if not TICKER_RE.fullmatch(ticker):
        raise CheckError(f"chain {chain_id}: malformed ticker {ticker!r}")
    if not name_well_formed(configs[ticker]):
        raise CheckError(f"chain {chain_id}: malformed underlying name {configs[ticker]!r}")
    return ticker + "s", configs[ticker] + " (ST0x)"


def check_list(chain_id, data, configs, instances):
    """Return the error messages for one token list."""
    errors = []
    covered = set()
    tickers = {ticker for ticker, _ in instances[chain_id].values()}
    for pending_chain, ticker in sorted(NOT_IN_DEPLOY_TABLE):
        if pending_chain == chain_id and ticker in tickers:
            errors.append(
                f"chain {chain_id}: {ticker} is in the st0x.deploy token table now; "
                "remove it from NOT_IN_DEPLOY_TABLE and run write"
            )
    for token in data["tokens"]:
        symbol = token.get("symbol")
        extensions = token.get("extensions", {})
        try:
            if token["chainId"] != chain_id:
                raise CheckError(f"{symbol} has chainId {token['chainId']}, list is {chain_id}")
            expected = expected_extensions(chain_id, token, configs, instances)
        except CheckError as error:
            errors.append(str(error))
            continue
        actual = (extensions.get(NEXT_SYMBOL), extensions.get(NEXT_NAME))
        if expected is None:
            if actual != (None, None):
                errors.append(f"chain {chain_id}: {symbol} is not renamed but has {actual}")
            continue
        if actual != expected:
            errors.append(f"chain {chain_id}: {symbol} has {actual}, expected {expected}")
            continue
        covered.add(token["address"].lower())

    if "tokenMap" in data:
        mirrored = {f"{t['chainId']}_{t['address']}": t for t in data["tokens"]}
        if data["tokenMap"] != mirrored:
            errors.append(f"chain {chain_id}: tokenMap does not mirror tokens")
    return errors, covered


def apply_list(chain_id, data, configs, instances):
    for token in data["tokens"]:
        expected = expected_extensions(chain_id, token, configs, instances)
        extensions = token.setdefault("extensions", {})
        extensions.pop(NEXT_SYMBOL, None)
        extensions.pop(NEXT_NAME, None)
        if expected is not None:
            extensions[NEXT_SYMBOL], extensions[NEXT_NAME] = expected
    if "tokenMap" in data:
        data["tokenMap"] = {f"{t['chainId']}_{t['address']}": t for t in data["tokens"]}


def read_list(path):
    raw = path.read_text(encoding="utf-8")
    indent = re.search(r"\n( +)\"", raw).group(1)
    return json.loads(raw), len(indent), raw.isascii()


def write_list(path, data, indent, ascii_only):
    text = json.dumps(data, indent=indent, ensure_ascii=ascii_only) + "\n"
    path.write_text(text, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["write", "check"])
    parser.add_argument("--deploy-dir", help=f"local st0x.deploy checkout at {DEPLOY_COMMIT}")
    args = parser.parse_args(argv)

    try:
        configs, instances = load_source(args.deploy_dir)
    except CheckError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    failed = False
    for chain_id, (filename, _) in CHAINS.items():
        path = TOKEN_LISTS / filename
        data, indent, ascii_only = read_list(path)
        if args.command == "write":
            try:
                apply_list(chain_id, data, configs, instances)
            except CheckError as error:
                print(f"error: {error}", file=sys.stderr)
                return 1
            write_list(path, data, indent, ascii_only)
            data, _, _ = read_list(path)
        errors, covered = check_list(chain_id, data, configs, instances)
        for error in errors:
            print(f"error: {filename}: {error}", file=sys.stderr)
        failed |= bool(errors)
        print(f"{filename}: {len(covered)} of {len(data['tokens'])} wrappers carry {NEXT_SYMBOL} and {NEXT_NAME}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
