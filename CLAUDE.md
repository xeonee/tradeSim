# tradeSim

## Why
A paper-trading simulator for Indian intraday cash equities — the foundation
for an eventual live day-trading bot. Built first as a simulator (not a live
bot) because the only way to trust a strategy with real money is to forward-
test it honestly, including realistic costs, before any capital is at risk.
The simulator and the future live bot share almost everything (feed interface,
strategy interface, portfolio, engine); only the execution backend differs.

## What
A small, broker-agnostic, pure-stdlib (no numpy/pandas) backtesting +
paper-trading engine:
- Bar-driven event loop with no look-ahead (decide on bar t, fill no earlier
  than bar t+1)
- Pluggable market data feeds:
  - `SyntheticIntradayFeed` — zero-setup random-walk bars for testing
  - `CsvBarFeed` — real historical bars from a CSV file
  - `AngelOneFeed` — live 1-minute bars for a single symbol via Angel One SmartAPI websocket
  - `AngelOneMultiFeed` — live 1-minute bars for multiple symbols (e.g. full NIFTY 50) on one connection
- Pluggable broker/fill engine:
  - `SimulatedBroker` — realistic fills with configurable spread, slippage, and partial fills
  - `AngelOneBroker` — live NSE order execution via Angel One SmartAPI
- Real Indian intraday-equity cost model (brokerage, STT, exchange txn fee,
  SEBI fee, stamp duty, GST) applied to every fill
- Portfolio accounting (cash, positions, realized/unrealized PnL, equity curve)
- Multi-symbol engine — strategies dispatched per symbol, square-off tracked per symbol
- Strategy interface + a toy `SmaCrossStrategy` (no expected edge — exists to
  exercise the engine, not to make money)
- Performance report: net/gross PnL, total costs, win rate, profit factor,
  max drawdown, annualised Sharpe

## How

### Run modes

```bash
# Synthetic feed — no setup needed
python3 run_demo.py

# Historical CSV (download first with fetch_data.py)
python3 run_demo.py --csv RELIANCE_1m.csv

# Live prices, paper (simulated) orders — single symbol
python3 run_demo.py --paper-trading --symbol RELIANCE

# Live prices, paper orders — full NIFTY 50 (run refresh_tokens.py first)
python3 run_demo.py --paper-trading --universe nifty50

# Live prices, REAL orders on NSE — single symbol
python3 run_demo.py --live-trading --symbol RELIANCE

# Live prices, REAL orders on NSE — full NIFTY 50
python3 run_demo.py --live-trading --universe nifty50
```

All modes write `trades.csv`, `equity_curve.csv`, and optionally `equity_curve.png`
(PNG needs matplotlib — fail soft if not installed).

### Credentials (for --paper-trading / --live-trading)

```bash
export ANGEL_API_KEY=...
export ANGEL_CLIENT_ID=...
export ANGEL_PASSWORD=...
export ANGEL_TOTP_SECRET=...
```

Get these from the Angel One SmartAPI developer console.

### Utility scripts

```bash
# Download historical NSE bars for any symbol → CSV
python3 fetch_data.py --symbol RELIANCE.NS --interval 1m --period 5d

# Find the Angel One exchange token for a symbol
python3 lookup_token.py RELIANCE

# Refresh NIFTY 50 token cache (run once, re-run after index rebalancing)
python3 refresh_tokens.py
```

### Project layout

```
trading_sim/
  models.py       - Bar, Order, Fill, Position, enums
  feed.py         - MarketDataFeed interface, SyntheticIntradayFeed, CsvBarFeed
  feed_angel.py   - AngelOneFeed, AngelOneMultiFeed, angel_login()
  broker.py       - Broker interface, SimulatedBroker (spread + slippage + partial fills)
  broker_angel.py - AngelOneBroker (live NSE orders via SmartAPI)
  costs.py        - IntradayEquityCostModel (NSE intraday charges, configurable)
  portfolio.py    - cash, positions, PnL, equity curve
  sizer.py        - PositionSizer (equal_capital / risk / volatility)
  strategy.py     - Strategy + Context interface, SmaCrossStrategy
  engine.py       - clock / event loop, per-symbol square-off, multi-strategy dispatch
  metrics.py      - performance_report() + format_report()
run_demo.py       - wires everything together, all run modes
fetch_data.py     - download historical bars via yfinance
lookup_token.py   - find Angel One exchange token for a symbol
refresh_tokens.py - regenerate nifty50_tokens.json from Angel One instrument master
```

### Conventions
- Stdlib-only in `trading_sim/` core; `feed_angel.py` and `broker_angel.py` require
  `smartapi-python` and `pyotp` but only when those code paths are used.
- Every new feature implements an existing interface (`MarketDataFeed`, `Broker`,
  `Strategy`) rather than special-casing the engine.
- Money: store/compute in plain `float` rupees, round only at display/fill
  boundaries. Costs always flow through `IntradayEquityCostModel`, never hardcoded inline.
- `SimulatedBroker` fill model: spread (`half_spread_pct`), market-impact slippage
  (`slippage_factor × qty/volume`), and partial fills (`vol_participation_cap`) — all
  configurable at construction, defaulting to conservative values.
- The engine accepts either a single `Strategy` or a `Dict[str, Strategy]` for
  multi-symbol runs. Square-off is tracked per symbol.

### SimulatedBroker fill model (configurable)
```python
SimulatedBroker(
    half_spread_pct=0.0005,     # 0.05% each side
    slippage_factor=0.05,       # 5% of (qty/volume) * price
    vol_participation_cap=0.25, # fill at most 25% of bar volume per LIMIT order
)
```

### Position sizing (`trading_sim/sizer.py`)

`PositionSizer` is passed to `SmaCrossStrategy` and computes qty dynamically
on every entry signal. Three methods, all configurable:

```python
PositionSizer(
    method="risk",           # "equal_capital" | "risk" | "volatility"
    capital=1_000_000,
    num_symbols=1,           # universe size (used by equal_capital)
    risk_per_trade=0.01,     # 1% of capital at risk per trade
    stop_pct=0.004,          # must match strategy stop (used by risk method)
    vol_lookback=20,         # bars of history for volatility estimation
    max_position_pct=0.20,   # hard cap: no position > 20% of capital
)
```

Select via CLI:
```bash
python3 run_demo.py --sizer risk            # default
python3 run_demo.py --sizer equal_capital
python3 run_demo.py --sizer volatility
python3 run_demo.py --sizer risk --risk-per-trade 0.005
```

### Known simplifications
- Cost rates are 2026 NSE intraday-equity defaults — re-verify against
  zerodha.com/charges periodically; they're config (`CostConfig`), not magic numbers.
- NIFTY 50 composition in `refresh_tokens.py` should be verified against NSE's
  official page after each index rebalancing (~every 6 months).

### Roadmap (all completed ✅)
1. ✅ Swap `SyntheticIntradayFeed` → `CsvBarFeed` with real historical bars
2. ✅ Realistic fills (spread, slippage, partial fills) — `broker.py` only
3. ✅ Live forward-paper-trading via Angel One websocket (`AngelOneFeed`)
4. ✅ Live `Broker` implementation against Angel One SmartAPI (`AngelOneBroker`)
5. ✅ Position sizing — equal capital, risk-based, volatility-based (`sizer.py`)

### Next steps
- Multi-symbol CSV backtesting support
- Strategy performance breakdown per symbol in the report
- Websocket reconnection logic for long-running sessions

### Not investment advice
`SmaCrossStrategy` is a toy with no claimed edge. Don't read its P&L as a
recommendation.
