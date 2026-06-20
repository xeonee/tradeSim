"""Simulation engine: the clock + event loop that ties everything together.

Per bar, in order:
  1. broker matches resting orders against this bar (fills from prior decisions)
  2. portfolio applies the fills
  3. portfolio marks to this bar's close
  4. intraday square-off check (force flat at/after square_off_time each day)
  5. record equity
  6. strategy sees the bar and may submit/cancel orders (which match NEXT bar)

Because the strategy acts in step 6 and matching happens in step 1, an order
decided on bar t fills no earlier than bar t+1 — no look-ahead.

The `strategy` parameter accepts either a single Strategy (single-symbol) or a
Dict[str, Strategy] (multi-symbol). Square-off and strategy dispatch are both
tracked per symbol so bars from different symbols never interfere.
"""
from __future__ import annotations

from datetime import time
from typing import Dict, Union

from .broker import SimulatedBroker
from .models import Bar
from .portfolio import Portfolio
from .strategy import Context, Strategy


class SimulationEngine:
    def __init__(self, feed, broker: SimulatedBroker, portfolio: Portfolio,
                 strategy: Union[Strategy, Dict[str, Strategy]],
                 square_off_time: time = time(15, 15)):
        self.feed = feed
        self.broker = broker
        self.portfolio = portfolio
        self.square_off_time = square_off_time
        # normalise to dict internally — single Strategy is wrapped with sentinel key
        if isinstance(strategy, dict):
            self._strategies: Dict[str, Strategy] = strategy
            self._single = False
        else:
            self._strategies = {"*": strategy}
            self._single = True

    def _get_strategy(self, symbol: str) -> Strategy:
        if self._single:
            return self._strategies["*"]
        return self._strategies.get(symbol)

    def run(self) -> Portfolio:
        ctx = Context(self.broker, self.portfolio)
        current_day = None
        squared_off: Dict[str, bool] = {}   # symbol → bool, reset each day

        for bar in self.feed:
            sym = bar.symbol

            if bar.ts.date() != current_day:
                current_day = bar.ts.date()
                squared_off.clear()

            for fill in self.broker.on_bar(bar):            # 1 + 2
                self.portfolio.apply(fill)
            self.portfolio.mark({sym: bar.close})           # 3

            if not squared_off.get(sym) and bar.ts.time() >= self.square_off_time:  # 4
                self._square_off(bar)
                squared_off[sym] = True

            self.portfolio.record_equity(bar.ts)            # 5

            if not squared_off.get(sym):                    # 6
                strat = self._get_strategy(sym)
                if strat:
                    ctx._bar = bar
                    strat.on_bar(bar, ctx)

        return self.portfolio

    def _square_off(self, bar: Bar) -> None:
        sym = bar.symbol
        for oid in list(self.broker.resting.keys()):
            if self.broker.resting[oid].symbol == sym:
                self.broker.cancel(oid)
        pos = self.portfolio.position(sym)
        fill = self.broker.force_liquidate(sym, pos.qty, bar.close, bar.ts)
        if fill is not None:
            self.portfolio.apply(fill)
        strat = self._get_strategy(sym)
        if strat:
            strat.on_square_off(bar)
