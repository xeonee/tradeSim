"""End-to-end demo of the trading simulator.

Runs an SMA-crossover TOY strategy and writes:
  - trades.csv         every fill with charges
  - equity_curve.csv   equity at each bar
  - equity_curve.png   equity curve plot (if matplotlib is installed)

Modes:
    python3 run_demo.py                              # synthetic feed (no setup)
    python3 run_demo.py --csv RELIANCE_1m.csv        # historical CSV

    # single symbol — token auto-looked up from nifty50_tokens.json or instrument master
    python3 run_demo.py --paper-trading --symbol RELIANCE
    python3 run_demo.py --live-trading  --symbol RELIANCE

    # all NIFTY 50 at once — run refresh_tokens.py first
    python3 run_demo.py --paper-trading --universe nifty50
    python3 run_demo.py --live-trading  --universe nifty50

--paper-trading and --live-trading read credentials from environment variables:
    ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.request
from datetime import time

from trading_sim import (IntradayEquityCostModel, Portfolio, SimulatedBroker,
                         SimulationEngine, SmaCrossStrategy, SyntheticIntradayFeed)
from trading_sim.broker_angel import AngelOneBroker
from trading_sim.feed import CsvBarFeed
from trading_sim.feed_angel import AngelOneFeed, AngelOneMultiFeed, angel_login
from trading_sim.metrics import format_report, performance_report

OUT = os.path.dirname(os.path.abspath(__file__))
NIFTY50_FILE = os.path.join(OUT, "nifty50_tokens.json")
_ANGEL_ENV   = ["ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"]
_INSTRUMENT_URL = (
    "https://margincalculator.angelbroking.com"
    "/OpenAPI_File/files/OpenAPIScripMaster.json"
)


def _detect_symbol(csv_path: str) -> str:
    with open(csv_path, newline="") as f:
        row = next(csv.DictReader(f), None)
    if row is None:
        raise ValueError(f"CSV is empty: {csv_path}")
    return row["symbol"]


def _load_nifty50() -> dict:
    if not os.path.exists(NIFTY50_FILE):
        sys.exit(
            "nifty50_tokens.json not found.\n"
            "Run:  python3 refresh_tokens.py   to generate it first."
        )
    with open(NIFTY50_FILE) as f:
        return json.load(f)


def _auto_lookup_token(symbol: str) -> tuple[str, str]:
    """Return (token, trading_symbol) for a symbol.

    Checks nifty50_tokens.json first (fast), then falls back to downloading
    the full instrument master (slow, ~5s).
    """
    if os.path.exists(NIFTY50_FILE):
        data = json.load(open(NIFTY50_FILE))
        if symbol in data:
            return data[symbol]["token"], data[symbol]["trading_symbol"]

    print(f"[lookup] {symbol} not in nifty50_tokens.json — downloading instrument master …")
    with urllib.request.urlopen(_INSTRUMENT_URL) as resp:
        instruments = json.load(resp)
    sym_upper = f"{symbol}-EQ"
    for inst in instruments:
        if inst.get("exch_seg") == "NSE" and inst.get("symbol", "").upper() == sym_upper:
            return inst["token"], inst["symbol"]
    sys.exit(
        f"Could not find token for '{symbol}'.\n"
        f"Run:  python3 lookup_token.py {symbol}   to see available matches."
    )


def _check_angel_env() -> None:
    missing = [k for k in _ANGEL_ENV if not os.getenv(k)]
    if missing:
        sys.exit(f"Missing environment variables: {', '.join(missing)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the trading simulator")
    parser.add_argument("--csv", metavar="PATH",
                        help="Historical bars CSV produced by fetch_data.py")
    parser.add_argument("--paper-trading", action="store_true",
                        help="Live Angel One feed + paper (simulated) broker")
    parser.add_argument("--live-trading", action="store_true",
                        help="Live Angel One feed + REAL broker (places actual NSE orders)")
    parser.add_argument("--symbol", metavar="NAME",
                        help="Single NSE symbol (e.g. RELIANCE)")
    parser.add_argument("--token", metavar="TOKEN",
                        help="Angel One exchange token — auto-looked up if omitted")
    parser.add_argument("--trading-symbol", metavar="TSYMBOL",
                        help="NSE trading symbol, e.g. RELIANCE-EQ — auto-inferred if omitted")
    parser.add_argument("--universe", metavar="NAME", choices=["nifty50"],
                        help="Trade a whole universe: nifty50 (requires refresh_tokens.py)")
    args = parser.parse_args()

    broker     = None
    strategies = None   # Dict[str, Strategy] for multi; set below

    # ------------------------------------------------------------------
    # Feed + strategy wiring
    # ------------------------------------------------------------------

    if args.paper_trading or args.live_trading:
        _check_angel_env()

        if args.universe == "nifty50":
            # ── NIFTY 50 mode ──────────────────────────────────────────
            universe = _load_nifty50()          # {sym: {token, trading_symbol}}
            symbol_token_map = {s: d["token"] for s, d in universe.items()}

            print(f"[Angel One] logging in …")
            smart, auth_token, feed_token = angel_login(
                api_key=os.environ["ANGEL_API_KEY"],
                client_id=os.environ["ANGEL_CLIENT_ID"],
                password=os.environ["ANGEL_PASSWORD"],
                totp_secret=os.environ["ANGEL_TOTP_SECRET"],
            )
            print(f"[Angel One] logged in — {len(universe)} symbols loaded")

            feed = AngelOneMultiFeed(
                symbol_token_map=symbol_token_map,
                api_key=os.environ["ANGEL_API_KEY"],
                client_id=os.environ["ANGEL_CLIENT_ID"],
                password=os.environ["ANGEL_PASSWORD"],
                totp_secret=os.environ["ANGEL_TOTP_SECRET"],
                auth_token=auth_token, feed_token=feed_token,
            )
            # one SmaCrossStrategy per symbol; qty=10 (equal-weight placeholder)
            strategies = {
                sym: SmaCrossStrategy(sym, fast=10, slow=30, qty=10, stop_pct=0.004)
                for sym in universe
            }
            symbol = "NIFTY50"   # label only, used in report title

            if args.live_trading:
                sym_token_full = {
                    s: (d["trading_symbol"], d["token"]) for s, d in universe.items()
                }
                _confirm_live(symbol, os.environ["ANGEL_CLIENT_ID"])
                broker = AngelOneBroker(
                    smart=smart, symbol_token_map=sym_token_full,
                    cost_model=IntradayEquityCostModel(),
                )

        elif args.symbol:
            # ── Single symbol mode ─────────────────────────────────────
            symbol = args.symbol.upper()
            if args.token and args.trading_symbol:
                token, trading_symbol = args.token, args.trading_symbol
            else:
                token, trading_symbol = _auto_lookup_token(symbol)
                if args.token:
                    token = args.token   # explicit token overrides lookup

            print(f"[Angel One] logging in …")
            smart, auth_token, feed_token = angel_login(
                api_key=os.environ["ANGEL_API_KEY"],
                client_id=os.environ["ANGEL_CLIENT_ID"],
                password=os.environ["ANGEL_PASSWORD"],
                totp_secret=os.environ["ANGEL_TOTP_SECRET"],
            )
            print(f"[Angel One] logged in as {os.environ['ANGEL_CLIENT_ID']}")

            feed = AngelOneFeed(
                symbol=symbol, exchange_token=token,
                api_key=os.environ["ANGEL_API_KEY"],
                client_id=os.environ["ANGEL_CLIENT_ID"],
                password=os.environ["ANGEL_PASSWORD"],
                totp_secret=os.environ["ANGEL_TOTP_SECRET"],
                auth_token=auth_token, feed_token=feed_token,
            )
            strategies = {symbol: SmaCrossStrategy(symbol, fast=10, slow=30,
                                                   qty=100, stop_pct=0.004)}

            if args.live_trading:
                _confirm_live(symbol, os.environ["ANGEL_CLIENT_ID"])
                broker = AngelOneBroker(
                    smart=smart,
                    symbol_token_map={symbol: (trading_symbol, token)},
                    cost_model=IntradayEquityCostModel(),
                )
        else:
            sys.exit("--paper-trading/--live-trading require --symbol or --universe nifty50")

    elif args.csv:
        feed   = CsvBarFeed(args.csv)
        symbol = _detect_symbol(args.csv)
        strategies = {symbol: SmaCrossStrategy(symbol, fast=10, slow=30,
                                               qty=100, stop_pct=0.004)}
    else:
        symbol = "ACME"
        feed   = SyntheticIntradayFeed(symbol=symbol, days=3, start_price=2500.0, seed=42)
        strategies = {symbol: SmaCrossStrategy(symbol, fast=10, slow=30,
                                               qty=100, stop_pct=0.004)}

    if broker is None:
        broker = SimulatedBroker(cost_model=IntradayEquityCostModel())

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    portfolio = Portfolio(starting_cash=1_000_000.0)
    engine    = SimulationEngine(feed, broker, portfolio, strategies,
                                 square_off_time=time(15, 15))
    engine.run()

    print(format_report(performance_report(portfolio)))

    with open(os.path.join(OUT, "trades.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "symbol", "side", "qty", "price", "charges", "tag"])
        for fl in portfolio.fills:
            w.writerow([fl.ts.isoformat(), fl.symbol, fl.side.value, fl.qty,
                        fl.price, fl.charges, fl.tag])

    with open(os.path.join(OUT, "equity_curve.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "equity"])
        for ts, eq in portfolio.equity_curve:
            w.writerow([ts.isoformat(), round(eq, 2)])

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        ts_vals = [t for t, _ in portfolio.equity_curve]
        eq_vals = [e for _, e in portfolio.equity_curve]
        plt.figure(figsize=(11, 5))
        plt.plot(ts_vals, eq_vals, linewidth=1.1)
        plt.axhline(portfolio.starting_cash, color="grey", linestyle="--", linewidth=0.8)
        plt.title(f"Equity curve - SMA crossover on {symbol} (after costs)")
        plt.ylabel("Equity (Rs)")
        plt.xlabel("Time")
        plt.tight_layout()
        plt.savefig(os.path.join(OUT, "equity_curve.png"), dpi=110)
        print("Wrote equity_curve.png")
    except ImportError:
        print("(matplotlib not installed - skipped equity_curve.png)")

    print("Wrote trades.csv and equity_curve.csv")


def _confirm_live(symbol: str, client_id: str) -> None:
    print("\n" + "!" * 60)
    print("  LIVE TRADING MODE: REAL orders will be placed on NSE.")
    print(f"  Symbol  : {symbol}")
    print(f"  Account : {client_id}")
    print("!" * 60)
    confirm = input("\nType CONFIRM to proceed, anything else to abort: ").strip()
    if confirm != "CONFIRM":
        sys.exit("Aborted.")


if __name__ == "__main__":
    main()
