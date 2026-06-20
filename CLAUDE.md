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
- Pluggable market data (`SyntheticIntradayFeed` for zero-setup runs,
  `CsvBarFeed` for real historical data)
- Pluggable broker/fill engine (`SimulatedBroker`, naive fills in v1: MARKET
  fills at next bar's open, LIMIT/STOP fill when price crosses — no spread, no
  slippage yet)
- Real Indian intraday-equity cost model (brokerage, STT, exchange txn fee,
  SEBI fee, stamp duty, GST) applied to every fill
- Portfolio accounting (cash, positions, realized/unrealized PnL, equity curve)
- Strategy interface + a toy `SmaCrossStrategy` (no expected edge — exists to
  exercise the engine, not to make money)
- Performance report: net/gross PnL, total costs, win rate, profit factor,
  max drawdown, annualised Sharpe

## How

### Run it
```bash
python3 run_demo.py
```
Writes `trades.csv`, `equity_curve.csv`, `equity_curve.png` (PNG needs
matplotlib — optional, everything else is pure stdlib).

### Project layout
```
trading_sim/
  models.py    - Bar, Order, Fill, Position, enums
  feed.py      - MarketDataFeed interface, SyntheticIntradayFeed, CsvBarFeed
  broker.py    - Broker interface, SimulatedBroker (the fill engine)
  costs.py     - IntradayEquityCostModel (NSE intraday charges, configurable)
  portfolio.py - cash, positions, PnL, equity curve
  strategy.py  - Strategy + Context interface, demo SmaCrossStrategy
  engine.py    - the clock / event loop, intraday square-off
  metrics.py   - performance_report() + format_report()
run_demo.py    - wires it all together
```

### Conventions
- Stdlib-only in `trading_sim/` core; only `run_demo.py`'s plotting step may
  use matplotlib, and only if installed (fail soft).
- Every new feature implements an existing interface (`MarketDataFeed`,
  `Broker`, `Strategy`) rather than special-casing the engine.
- Money: store/compute in plain `float` rupees, round only at display/fill
  boundaries. Costs always flow through `IntradayEquityCostModel`, never
  hardcoded inline.
- No network calls and no broker API calls anywhere in this repo yet — that's
  a deliberate v1 boundary, not an oversight.

### Known v1 simplifications (the honest list)
- Fills have no spread or slippage — realistic for "does the machinery work,"
  not for "is this strategy good." Upgrade lives entirely in `broker.py`.
- Single-symbol demo (Portfolio itself is already multi-symbol).
- Cost rates are 2026 NSE intraday-equity defaults — re-verify against
  zerodha.com/charges periodically; they're config (`CostConfig`), not magic
  numbers.

### Roadmap (next steps, roughly in order)
1. Swap `SyntheticIntradayFeed` → `CsvBarFeed` with real historical bars
2. Realistic fills (spread, slippage, partial fills) — edit `broker.py` only
3. Live forward-paper-trading via a websocket-backed `MarketDataFeed`
4. Go-live `Broker` implementation against a real broker API (Upstox/Zerodha) —
   requires a static IP per current SEBI algo-trading rules; out of scope here

### Not investment advice
`SmaCrossStrategy` is a toy with no claimed edge. Don't read its P&L as a
recommendation.
