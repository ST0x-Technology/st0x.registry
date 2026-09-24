# Prints one line per problem, nothing when the file is sound. Input is the
# TOML already parsed strictly (tomllib), so duplicate keys never get here.

def addr: type == "string" and test("^0x[0-9a-fA-F]{40}$");
def flag: . == "enabled" or . == "disabled";
# An integer as written: jq keeps the literal, so 1.0 stays "1.0" here even
# though 1.0 == 1. The services read these as integers and would reject a float.
def int: type == "number" and (tojson | test("^-?[0-9]+$"));
def text: type == "string" and length > 0;
def unknown($allowed; $where):
  keys[] | select(IN($allowed[]) | not) | "\($where): unknown key \(.)";
def bad(cond; msg): if cond then msg else empty end;

. as $root
| ($root.chains // {}) as $chains
| ($root.assets.equities // {}) as $assets
| ([$chains[] | (.assets.equities // {}) | to_entries[]
    | select(.value.pricing == "enabled") | .key] | unique) as $priced
| [$root.trading_state.scopes[]?.assets[]] as $scoped
|
  bad(($root.schema_version | int | not) or $root.schema_version != 1;
      "schema_version must be the integer 1, got \($root.schema_version | tojson)"),
  ($root | unknown(["schema_version", "chains", "assets", "trading_state"]; "top level")),
  (($root.assets // {}) | unknown(["equities"]; "assets")),
  (($root.trading_state // {}) | unknown(["scopes"]; "trading_state")),

  # Chains: pricing's [[chains]] plus bebop's per-chain vault.
  ($chains | to_entries[] | .key as $c | .value as $ch
   | ($ch | unknown(["chain_id", "quote_token", "contract", "inventory", "assets"]; $c)),
     (($ch.assets // {}) | unknown(["equities"]; "\($c).assets")),
     bad($ch.chain_id | int | not; "\($c): chain_id must be an integer"),
     bad($ch.quote_token | addr | not; "\($c): quote_token is not an address"),
     bad(($ch.contract == null) != ($ch.inventory == null); "\($c): contract and inventory go together"),
     ([$ch.contract, $ch.inventory][] | select(. != null and (addr | not)) | "\($c): bad address \(.)"),

     # One token on one chain.
     (($ch.assets.equities // {}) | to_entries[] | .key as $s | .value as $a | "\($c) \($s)" as $w
      | ($a | unknown(["tokenized_equity", "tokenized_equity_derivative", "vault_ids", "pricing",
                       "venues", "trading", "rebalancing", "wrapped_equity_recovery",
                       "operational_limit", "target_share"]; $w)),
        bad($assets[$s] == null; "\($w): no [assets.equities.\($s)] block"),
        bad($a.tokenized_equity_derivative | addr | not; "\($w): tokenized_equity_derivative is not an address"),
        bad($a.pricing | flag | not; "\($w): pricing must be enabled or disabled"),
        bad(($a.venues | type) != "array"; "\($w): venues must be a list"),
        (($a.venues // [])[] | select(IN("bebop", "raindex", "hook") | not) | "\($w): unknown venue \(.)"),
        bad($a.pricing != "enabled" and (($a.venues // []) | length) > 0;
            "\($w): has venues but pricing is disabled, so no venue gets a price"),
        # vault_ids is optional (liquidity defaults it to empty) but never empty
        # when written, and bebop needs it wherever it quotes from a vault.
        bad($a.vault_ids != null and (($a.vault_ids | type) != "array" or ($a.vault_ids | length) == 0
            or ($a.vault_ids | any(type != "string" or (test("^0x[0-9a-fA-F]{1,64}$") | not))));
            "\($w): vault_ids must be a non-empty list of hex ids"),
        bad($ch.contract != null and IN(($a.venues // [])[]; "bebop") and $a.vault_ids == null;
            "\($w): quoted on bebop from a vault but has no vault_ids"),
        # Liquidity's row: all of it or none of it.
        (if [$a.tokenized_equity, $a.trading, $a.rebalancing, $a.wrapped_equity_recovery] | any(. != null)
         then bad($a.tokenized_equity | addr | not; "\($w): tokenized_equity is not an address"),
              ([$a.trading, $a.rebalancing, $a.wrapped_equity_recovery][]
               | select(flag | not) | "\($w): trading/rebalancing/wrapped_equity_recovery must each be enabled or disabled")
         else empty end))),

  # One token, every chain: spread and hedging policy.
  ($assets | to_entries[] | .key as $s | .value as $a
   | ($a | unknown(["fixed_half_spread_bps", "extended_hours_counter_trading", "spread_model"]; $s)),
     bad($a.fixed_half_spread_bps != null and (($a.fixed_half_spread_bps | type) != "number"
         or $a.fixed_half_spread_bps <= 0 or $a.fixed_half_spread_bps >= 10000);
         "\($s): fixed_half_spread_bps must be between 0 and 10000"),
     bad($a.extended_hours_counter_trading != null and ($a.extended_hours_counter_trading | flag | not);
         "\($s): extended_hours_counter_trading must be enabled or disabled"),
     bad($a.fixed_half_spread_bps != null and (($a.spread_model.profiles // []) | length) > 0;
         "\($s): has both a fixed spread and profiles; pricing takes one or the other"),
     bad(IN($priced[]; $s) and $a.fixed_half_spread_bps == null and (($a.spread_model.profiles // []) | length) == 0;
         "\($s): priced but has no spread, so it would never quote"),
     (($a.spread_model // {}) | unknown(["profiles"]; "\($s) spread_model")),
     (($a.spread_model.profiles // [])[] | . as $p
      | unknown(["session", "venue", "half_spread_bps", "quote_staleness_seconds"]; "\($s) profile"),
        bad(IN($p.session; "rth", "pre", "post") | not; "\($s): profile session must be rth, pre or post"),
        bad($p.venue != null and (IN($p.venue; "bebop", "raindex", "hook") | not); "\($s): profile venue \($p.venue)"),
        bad(($p.half_spread_bps | type) != "number" or $p.half_spread_bps <= 0 or $p.half_spread_bps >= 10000;
            "\($s): profile half_spread_bps must be between 0 and 10000"),
        bad(($p.quote_staleness_seconds | type) != "number" or $p.quote_staleness_seconds <= 0;
            "\($s): profile quote_staleness_seconds must be positive"))),

  # Each trading_state scope, as pricing's TradingScope reads it.
  ($root.trading_state.scopes // [] | to_entries[] | .value as $sc | "trading_state scope \(.key + 1)" as $w
   | ($sc | unknown(["id", "profile_revision", "sessions", "assets"]; $w)),
     bad($sc.id | text | not; "\($w): id missing"),
     bad($sc.profile_revision | text | not; "\($w): profile_revision missing"),
     bad(IN($sc.sessions; "regular", "extended") | not; "\($w): sessions must be regular or extended"),
     bad(($sc.assets | type) != "array" or ($sc.assets | any(text | not)); "\($w): assets must be a list of tickers")),
  ([$root.trading_state.scopes[]?.id] | group_by(.) | map(select(length > 1) | .[0]) | .[]
   | "trading_state: scope id \(.) is used twice"),

  # Pricing refuses to start unless every priced token is in exactly one scope.
  ($priced - $scoped | .[] | "\(.): priced but in no trading_state scope"),
  ($scoped - $priced | .[] | "\(.): in a trading_state scope but not priced anywhere"),
  ($scoped | group_by(.) | map(select(length > 1) | .[0]) | .[] | "\(.): in more than one trading_state scope")
