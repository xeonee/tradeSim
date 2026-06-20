"""Portfolio accounting: cash, positions, realized/unrealized PnL, equity curve."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Tuple

from .models import Fill, Position, Side


class Portfolio:
    def __init__(self, starting_cash: float = 1_000_000.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.positions: Dict[str, Position] = {}
        self.last_price: Dict[str, float] = {}
        self.fills: List[Fill] = []
        self.total_charges = 0.0
        self.trade_pnls: List[float] = []  # realized PnL per closing fill (net of that fill's charges)
        self.equity_curve: List[Tuple[datetime, float]] = []

    def position(self, symbol: str) -> Position:
        return self.positions.setdefault(symbol, Position(symbol))

    def apply(self, fill: Fill) -> None:
        pos = self.position(fill.symbol)
        notional = fill.price * fill.qty
        self.cash += (-notional if fill.side is Side.BUY else notional) - fill.charges
        self.total_charges += fill.charges
        realized = pos.apply(fill.side, fill.qty, fill.price)
        if realized != 0.0:
            self.trade_pnls.append(realized - fill.charges)
        self.last_price[fill.symbol] = fill.price
        self.fills.append(fill)

    def mark(self, prices: Dict[str, float]) -> None:
        self.last_price.update(prices)

    def equity(self) -> float:
        mv = sum(p.qty * self.last_price.get(sym, p.avg_price)
                 for sym, p in self.positions.items())
        return self.cash + mv

    def record_equity(self, ts: datetime) -> None:
        self.equity_curve.append((ts, self.equity()))
