"""Execution layer.

The strategy and engine only know the Broker interface: submit(order),
cancel(order_id), and per-bar matching. SimulatedBroker now models three
sources of execution friction:

  Spread   — BUY fills half_spread_pct above the reference price;
              SELL fills half_spread_pct below it.
  Slippage — market-impact: fill worsens by slippage_factor * (qty/bar_volume)
              as a fraction of price (larger order vs. thinner bar = worse fill).
  Partial  — LIMIT orders are capped at vol_participation_cap of bar volume per
              bar; the remainder rests and may fill on a future bar.

Order types:
  MARKET : fills at next bar's open, with spread + slippage.
  LIMIT  : fills at limit price (no slippage) when bar range crosses it;
            subject to partial-fill volume cap.
  STOP   : triggers when bar range crosses stop_price, then fills like a
            market order (spread + slippage) clamped to the bar's range.

All three params default to zero-ish conservative values so the model stays
honest on thin synthetic data while being easy to tune for realistic data.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from .costs import IntradayEquityCostModel
from .models import Bar, Fill, Order, OrderStatus, OrderType, Side


class Broker:
    def submit(self, order: Order) -> int: raise NotImplementedError
    def cancel(self, order_id: int) -> bool: raise NotImplementedError
    def on_bar(self, bar: Bar) -> List[Fill]: raise NotImplementedError


class SimulatedBroker(Broker):
    def __init__(self,
                 cost_model: Optional[IntradayEquityCostModel] = None,
                 half_spread_pct: float = 0.0005,
                 slippage_factor: float = 0.05,
                 vol_participation_cap: float = 0.25):
        self.cost_model = cost_model or IntradayEquityCostModel()
        self.half_spread_pct = half_spread_pct
        self.slippage_factor = slippage_factor
        self.vol_participation_cap = vol_participation_cap
        self.resting: Dict[int, Order] = {}
        self.last_price: Dict[str, float] = {}

    def submit(self, order: Order) -> int:
        order.status = OrderStatus.OPEN
        self.resting[order.id] = order
        return order.id

    def cancel(self, order_id: int) -> bool:
        o = self.resting.pop(order_id, None)
        if o is not None:
            o.status = OrderStatus.CANCELLED
            return True
        return False

    def _market_fill_price(self, side: Side, ref_price: float,
                           qty: int, bar_volume: float) -> float:
        """Spread + market-impact slippage around a reference price."""
        direction = 1 if side is Side.BUY else -1
        spread = ref_price * self.half_spread_pct * direction
        vol_ratio = min(qty / bar_volume, 1.0) if bar_volume > 0 else 0.0
        slippage = ref_price * self.slippage_factor * vol_ratio * direction
        return ref_price + spread + slippage

    def _fill(self, order: Order, fill_qty: int, price: float, ts: datetime) -> Fill:
        charges = self.cost_model.charges(order.side, price, fill_qty)
        prev_filled = order.filled_qty
        order.filled_qty += fill_qty
        # weighted-average fill price across partial fills
        if prev_filled == 0:
            order.avg_fill_price = price
        else:
            order.avg_fill_price = (
                (order.avg_fill_price * prev_filled + price * fill_qty) / order.filled_qty
            )
        order.status = (OrderStatus.FILLED
                        if order.filled_qty >= order.qty
                        else OrderStatus.PARTIALLY_FILLED)
        order.ts_updated = ts
        return Fill(order_id=order.id, symbol=order.symbol, side=order.side,
                    qty=fill_qty, price=round(price, 2), ts=ts,
                    charges=round(charges, 2), tag=order.tag)

    def on_bar(self, bar: Bar) -> List[Fill]:
        self.last_price[bar.symbol] = bar.close
        fills: List[Fill] = []
        for oid in list(self.resting.keys()):
            o = self.resting.get(oid)
            if o is None or o.symbol != bar.symbol:
                continue

            remaining = o.qty - o.filled_qty
            fill_price: Optional[float] = None
            fill_qty: Optional[int] = None

            if o.order_type is OrderType.MARKET:
                fill_price = self._market_fill_price(o.side, bar.open, remaining, bar.volume)
                fill_qty = remaining  # market orders always fill in full

            elif o.order_type is OrderType.LIMIT:
                triggered = (
                    (o.side is Side.BUY  and bar.low  <= o.limit_price) or
                    (o.side is Side.SELL and bar.high >= o.limit_price)
                )
                if triggered:
                    fill_price = o.limit_price  # limit: you get your price, no slippage
                    if bar.volume > 0:
                        available = int(bar.volume * self.vol_participation_cap)
                        fill_qty = min(remaining, max(1, available))
                    else:
                        fill_qty = remaining

            elif o.order_type is OrderType.STOP:
                triggered = (
                    (o.side is Side.BUY  and bar.high >= o.stop_price) or
                    (o.side is Side.SELL and bar.low  <= o.stop_price)
                )
                if triggered:
                    raw = self._market_fill_price(o.side, o.stop_price, remaining, bar.volume)
                    # clamp to the bar's actual range — can't fill outside it
                    fill_price = max(bar.low, min(bar.high, raw))
                    fill_qty = remaining

            if fill_price is not None and fill_qty is not None and fill_qty > 0:
                fills.append(self._fill(o, fill_qty, fill_price, bar.ts))
                if o.status is OrderStatus.FILLED:
                    del self.resting[oid]
                # PARTIALLY_FILLED orders stay resting for the next bar

        return fills

    def force_liquidate(self, symbol: str, signed_qty: int,
                        price: float, ts: datetime) -> Optional[Fill]:
        """Immediate market exit at square-off time (spread applied, no slippage)."""
        if signed_qty == 0:
            return None
        side = Side.SELL if signed_qty > 0 else Side.BUY
        direction = 1 if side is Side.BUY else -1
        fill_price = price + price * self.half_spread_pct * direction
        order = Order(symbol=symbol, side=side, qty=abs(signed_qty),
                      order_type=OrderType.MARKET, tag="squareoff")
        order.status = OrderStatus.OPEN
        return self._fill(order, abs(signed_qty), fill_price, ts)
