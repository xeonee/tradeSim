"""Download historical OHLCV bars from Yahoo Finance and save to a CSV
that CsvBarFeed can read directly.

Usage examples:
    # Last 5 days of 1-minute bars for Reliance (max 7d for 1m via Yahoo)
    python3 fetch_data.py --symbol RELIANCE.NS --interval 1m --period 5d

    # 5-minute bars for TCS over a custom date range
    python3 fetch_data.py --symbol TCS.NS --interval 5m --start 2025-01-01 --end 2025-06-01

    # Daily bars for INFY for the past year
    python3 fetch_data.py --symbol INFY.NS --interval 1d --period 1y

Yahoo Finance interval limits:
    1m  — max 7 days of history
    5m  — max 60 days
    15m — max 60 days
    1h  — max 730 days
    1d  — unlimited

Output CSV columns: ts,symbol,open,high,low,close,volume
(ISO-8601 timestamps, no timezone suffix — local IST)
"""
from __future__ import annotations

import argparse
import csv
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download NSE historical bars via yfinance → CsvBarFeed-compatible CSV"
    )
    parser.add_argument("--symbol", required=True,
                        help="Yahoo Finance ticker, e.g. RELIANCE.NS, TCS.NS, NIFTY50.NS")
    parser.add_argument("--interval", default="1m",
                        choices=["1m", "5m", "15m", "1h", "1d"],
                        help="Bar interval (default: 1m)")
    parser.add_argument("--period", default="5d",
                        help="Lookback period, e.g. 5d, 1mo, 1y (ignored if --start/--end given)")
    parser.add_argument("--start", help="Start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end",   help="End date YYYY-MM-DD (exclusive)")
    parser.add_argument("--out",   help="Output CSV path (default: <SYMBOL>_<interval>.csv)")
    args = parser.parse_args()

    try:
        import yfinance as yf
    except ImportError:
        sys.exit("yfinance is not installed. Run:  pip install yfinance")

    print(f"Fetching {args.symbol} [{args.interval}] from Yahoo Finance …")
    ticker = yf.Ticker(args.symbol)

    if args.start or args.end:
        hist = ticker.history(interval=args.interval, start=args.start, end=args.end)
    else:
        hist = ticker.history(interval=args.interval, period=args.period)

    if hist.empty:
        sys.exit(
            f"No data returned for {args.symbol!r}. "
            "Check the ticker symbol (needs .NS suffix for NSE) and date range."
        )

    # Convert tz-aware index → IST naive (the rest of the sim is tz-naive)
    if getattr(hist.index, "tz", None) is not None:
        hist.index = hist.index.tz_convert("Asia/Kolkata").tz_localize(None)

    # For intraday intervals keep only NSE session hours (09:15 – 15:30)
    if args.interval != "1d":
        hist = hist.between_time("09:15", "15:30")

    if hist.empty:
        sys.exit("After filtering to NSE hours no bars remain. Try a wider date range.")

    # Use the bare ticker name (drop exchange suffix) as the CSV symbol column
    symbol_name = args.symbol.split(".")[0]

    out_path = args.out or f"{symbol_name}_{args.interval}.csv"

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "symbol", "open", "high", "low", "close", "volume"])
        for ts, row in hist.iterrows():
            writer.writerow([
                ts.isoformat(),
                symbol_name,
                round(float(row["Open"]),   2),
                round(float(row["High"]),   2),
                round(float(row["Low"]),    2),
                round(float(row["Close"]),  2),
                int(row["Volume"]),
            ])

    print(f"Wrote {len(hist)} bars → {out_path}")
    print(f"\nNext step:")
    print(f"  python3 run_demo.py --csv {out_path}")


if __name__ == "__main__":
    main()
