"""Download Angel One's instrument master and write nifty50_tokens.json.

Run this once before using --universe nifty50, and again whenever the NIFTY 50
composition changes (NSE revises it roughly every 6 months).

    python3 refresh_tokens.py

Verify the current NIFTY 50 composition at:
    https://www.nseindia.com/products-services/indices-nifty50-index
"""
from __future__ import annotations

import json
import sys
import urllib.request

INSTRUMENT_URL = (
    "https://margincalculator.angelbroking.com"
    "/OpenAPI_File/files/OpenAPIScripMaster.json"
)
OUT = "nifty50_tokens.json"

# NSE symbols as they appear in Angel One's instrument master (without -EQ).
# Verify against NSE's official page if composition has changed.
NIFTY50_SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "BRITANNIA", "CIPLA", "COALINDIA", "DIVISLAB", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK",
    "INFY", "ITC", "JSWSTEEL", "KOTAKBANK", "LT",
    "LTIM", "M&M", "MARUTI", "NESTLEIND", "NTPC",
    "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
    "SHRIRAMFIN", "SUNPHARMA", "TATAMOTORS", "TATASTEEL", "TATACONSUM",
    "TCS", "TECHM", "TITAN", "ULTRACEMCO", "WIPRO",
]


def build_index(instruments: list) -> dict:
    """Build a fast lookup: uppercase(symbol) → instrument entry for NSE EQ."""
    index = {}
    for inst in instruments:
        if inst.get("exch_seg") != "NSE":
            continue
        sym = inst.get("symbol", "").upper()
        index.setdefault(sym, inst)
    return index


def main() -> None:
    print("Downloading instrument master from Angel One …")
    with urllib.request.urlopen(INSTRUMENT_URL) as resp:
        instruments = json.load(resp)
    print(f"  {len(instruments):,} instruments loaded.")

    index = build_index(instruments)
    result: dict = {}
    missing: list[str] = []

    for sym in NIFTY50_SYMBOLS:
        trading_sym = f"{sym}-EQ"
        entry = index.get(trading_sym.upper()) or index.get(sym.upper())
        if entry:
            result[sym] = {
                "token":          entry["token"],
                "trading_symbol": entry["symbol"],
            }
        else:
            missing.append(sym)

    if missing:
        print(f"\nWARNING — could not find tokens for: {', '.join(missing)}")
        print("These symbols will be skipped in --universe nifty50 mode.")
        print("Check if the symbol name differs in Angel One's master (run lookup_token.py).")

    with open(OUT, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\nWrote {len(result)}/50 symbols → {OUT}")
    if not missing:
        print("All 50 symbols resolved. You're ready to run:")
        print("  python3 run_demo.py --paper-trading --universe nifty50")


if __name__ == "__main__":
    main()
