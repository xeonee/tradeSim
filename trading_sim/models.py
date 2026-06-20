"""Core data types for the trading simulator.

Plain dataclasses/enums with no external dependencies, so the engine stays
portable and easy to read. Everything the strategy, broker, portfolio and engine
pass around is defined here.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"  # stop-market: triggers to a market fill when price crosses stop_price


class OrderStatus(Enum):
    PENDING = "PENDING"                    # created, not yet seen by the matching loop
    OPEN = "OPEN"                          # resting, waiting to fill / trigger
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"  # reserved for a future, more realistic fill model
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class Bar:
    """A single OHLCV candle for one symbol at one timestamp."""
    ts: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


_order_ids = itertools.count(1)


@dataclass
class Order:
    symbol: str
    side: Side
    qty: int
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    tag: str = ""  # free-form label the strategy can use ("entry", "stop", "exit", "squareoff")
    id: int = field(default_factory=lambda: next(_order_ids))
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    ts_created: Optional[datetime] = None
    ts_updated: Optional[datetime] = None

    def __post_init__(self):
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("LIMIT order requires limit_price")
        if self.order_type is OrderType.STOP and self.stop_price is None:
            raise ValueError("STOP order requires stop_price")


@dataclass
class Fill:
    order_id: int
    symbol: str
    side: Side
    qty: int
    price: float
    ts: datetime
    charges: float = 0.0
    tag: str = ""


@dataclass
class Position:
    symbol: str
    qty: int = 0          # signed: positive = long, negative = short
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    def apply(self, side: Side, qty: int, price: float) -> float:
        """Apply a fill; return realized PnL from this fill (gross of charges)."""
        signed = side.sign * qty
        realized = 0.0
        if self.qty == 0 or (self.qty > 0) == (signed > 0):
            # opening or adding in the same direction -> weighted-average price
            new_qty = self.qty + signed
            self.avg_price = (self.avg_price * abs(self.qty) + price * abs(signed)) / abs(new_qty)
            self.qty = new_qty
        else:
            # reducing, closing, or flipping through zero
            closing = min(abs(signed), abs(self.qty))
            if self.qty > 0:
                realized = (price - self.avg_price) * closing   # long, selling
            else:
                realized = (self.avg_price - price) * closing   # short, buying
            new_qty = self.qty + signed
            if new_qty == 0:
                self.avg_price = 0.0
            elif (self.qty > 0) != (new_qty > 0):
                self.avg_price = price  # flipped: leftover opens a new position at fill price
            # else: simple reduction -> avg_price unchanged
            self.qty = new_qty
        self.realized_pnl += realized
        return realized
