"""Strategy interface + a demo SMA-crossover strategy.

A Strategy implements on_bar(bar, ctx). The SAME strategy code runs in
simulation and (later) live, because Context only exposes broker-agnostic
operations: read your position, read the last price, submit/cancel orders.

SmaCrossStrategy is a TOY whose only job is to exercise the engine end to end
(market entry, protective stop, signal exit, daily square-off). It is NOT a
recommendation and has no expected edge — on the synthetic random-walk feed it
will roughly break even before costs and lose after them.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Optional

from .models import Bar, Order, OrderType, Side

if TYPE_CHECKING:
    from .sizer import PositionSizer


class Context:
    def __init__(self, broker, portfolio):
        self._broker = broker
        self._portfolio = portfolio
        self._bar: Optional[Bar] = None

    def position_qty(self, symbol: str) -> int:
        return self._portfolio.position(symbol).qty

    def last_price(self, symbol: str) -> float:
        return self._broker.last_price.get(symbol, 0.0)

    def submit(self, order: Order) -> int:
        order.ts_created = self._bar.ts if self._bar else None
        return self._broker.submit(order)

    def cancel(self, order_id: int) -> bool:
        return self._broker.cancel(order_id)


class Strategy:
    def on_bar(self, bar: Bar, ctx: Context) -> None:
        raise NotImplementedError

    def on_square_off(self, bar: Bar) -> None:
        pass


class SmaCrossStrategy(Strategy):
    def __init__(self, symbol: str, fast: int = 10, slow: int = 30,
                 qty: int = 100, stop_pct: float = 0.004,
                 sizer: Optional["PositionSizer"] = None):
        self.symbol   = symbol
        self.fast     = fast
        self.slow     = slow
        self.qty      = qty          # fallback fixed qty when sizer is None
        self.stop_pct = stop_pct
        self.sizer    = sizer
        self.closes: deque = deque(maxlen=slow)
        self.prev_fast: Optional[float] = None
        self.prev_slow: Optional[float] = None
        self.stop_order_id: Optional[int] = None
        self._entry_qty: int = 0     # actual qty submitted on entry (for matching exit)

    @staticmethod
    def _sma(values: deque, n: int) -> Optional[float]:
        if len(values) < n:
            return None
        v = list(values)[-n:]
        return sum(v) / n

    def _resolve_qty(self, price: float) -> int:
        if self.sizer is not None:
            return self.sizer.qty(
                price=price,
                stop_pct=self.stop_pct,
                recent_closes=list(self.closes),
            )
        return self.qty

    def on_bar(self, bar: Bar, ctx: Context) -> None:
        self.closes.append(bar.close)
        fast     = self._sma(self.closes, self.fast)
        slow     = self._sma(self.closes, self.slow)
        qty_held = ctx.position_qty(self.symbol)

        # if the protective stop fired and we're now flat, clean up stale id
        if qty_held == 0 and self.stop_order_id is not None:
            ctx.cancel(self.stop_order_id)
            self.stop_order_id  = None
            self._entry_qty     = 0

        if (fast is not None and slow is not None
                and self.prev_fast is not None and self.prev_slow is not None):
            bull_cross = self.prev_fast <= self.prev_slow and fast > slow
            bear_cross = self.prev_fast >= self.prev_slow and fast < slow

            if bull_cross and qty_held == 0:
                entry_qty = self._resolve_qty(bar.close)
                self._entry_qty = entry_qty
                ctx.submit(Order(self.symbol, Side.BUY, entry_qty,
                                 OrderType.MARKET, tag="entry"))
                stop_px = round(bar.close * (1 - self.stop_pct), 2)
                self.stop_order_id = ctx.submit(
                    Order(self.symbol, Side.SELL, entry_qty, OrderType.STOP,
                          stop_price=stop_px, tag="stop"))

            elif bear_cross and qty_held > 0:
                if self.stop_order_id is not None:
                    ctx.cancel(self.stop_order_id)
                    self.stop_order_id = None
                # exit exactly what we hold (accounts for partial fills)
                ctx.submit(Order(self.symbol, Side.SELL, qty_held,
                                 OrderType.MARKET, tag="exit"))
                self._entry_qty = 0

        self.prev_fast, self.prev_slow = fast, slow

    def on_square_off(self, bar: Bar) -> None:
        self.stop_order_id = None
        self.prev_fast = self.prev_slow = None
        self.closes.clear()
        self._entry_qty = 0
