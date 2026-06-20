"""Look up the Angel One exchange token for an NSE symbol.

Angel One requires an "exchange token" (a numeric string like "2885") to
subscribe to a symbol's live feed. This script downloads Angel One's
instrument master and searches it by symbol name.

Usage:
    python3 lookup_token.py RELIANCE
    python3 lookup_token.py TCS
    python3 lookup_token.py NIFTY
"""
from __future__ import annotations

import json
import sys
import urllib.request

INSTRUMENT_URL = (
    "https://margincalculator.angelbroking.com"
    "/OpenAPI_File/files/OpenAPIScripMaster.json"
)


def lookup_token(symbol: str, exchange: str = "NSE") -> list[dict]:
    print("Downloading instrument master from Angel One …")
    with urllib.request.urlopen(INSTRUMENT_URL) as resp:
        instruments = json.load(resp)

    query = symbol.strip().upper()

    # 1. Exact match on symbol field (e.g. "RELIANCE-EQ")
    matches = [
        i for i in instruments
        if i.get("exch_seg") == exchange and i.get("symbol", "").upper() == query
    ]

    # 2. Try appending -EQ (most NSE cash equities are stored as SYMBOL-EQ)
    if not matches:
        matches = [
            i for i in instruments
            if i.get("exch_seg") == exchange and i.get("symbol", "").upper() == f"{query}-EQ"
        ]

    # 3. Partial match on symbol or company name so the user can see candidates
    if not matches:
        matches = [
            i for i in instruments
            if i.get("exch_seg") == exchange
            and (query in i.get("symbol", "").upper() or query in i.get("name", "").upper())
        ]

    return matches


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: python3 lookup_token.py <SYMBOL>  e.g.  RELIANCE")

    symbol = " ".join(sys.argv[1:])
    matches = lookup_token(symbol)

    if not matches:
        print(f"No match found for '{symbol}' on NSE.")
        sys.exit(1)

    print(f"\nMatches for '{symbol}':")
    print(f"{'Token':<10} {'Symbol':<25} {'Name':<40} {'Type'}")
    print("-" * 90)
    for m in matches[:20]:   # cap at 20 so output doesn't flood the terminal
        print(f"{m.get('token',''):<10} {m.get('symbol',''):<25} "
              f"{m.get('name',''):<40} {m.get('instrumenttype','')}")

    if len(matches) > 20:
        print(f"  … and {len(matches) - 20} more. Narrow your search term.")

    best = matches[0]
    print(f"\nTo use the first match ({best.get('symbol')}) with run_demo.py:")
    print(f"  python3 run_demo.py --paper-trading --symbol {symbol.split()[0]} "
          f"--token {best.get('token', '???')} "
          f"--trading-symbol {best.get('symbol', '???')}")


if __name__ == "__main__":
    main()
