"""Live broker implementation backed by Angel One SmartAPI.

Implements the same Broker interface as SimulatedBroker so the engine,
strategy, and portfolio are completely untouched. The only difference:
orders go to NSE via Angel One instead of being matched locally.

WARNING — this places REAL orders with REAL money. Only use this after
thorough paper-trading validation with SimulatedBroker + AngelOneFeed.

Order type mapping:
    MARKET → variety=NORMAL,    ordertype=MARKET
    LIMIT  → variety=NORMAL,    ordertype=LIMIT
    STOP   → variety=STOPLOSS,  ordertype=STOPLOSS_MARKET

Fills are discovered by polling orderBook() on each on_bar() call.
force_liquidate() places a market order and polls until confirmed (or
falls back to last known price after a timeout).
"""
from __future__ import annotations

import time as _time
from datetime import datetime
from typing import Dict, List, Optional

from .costs import IntradayEquityCostModel
from .models import Bar, Fill, Order, OrderStatus, OrderType, Side
from .broker import Broker


_VARIETY = {
    OrderType.MARKET: "NORMAL",
    OrderType.LIMIT:  "NORMAL",
    OrderType.STOP:   "STOPLOSS",
}
_ORDER_TYPE = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT:  "LIMIT",
    OrderType.STOP:   "STOPLOSS_MARKET",
}
_ANGEL_FILLED    = "complete"
_ANGEL_CANCELLED = {"cancelled", "rejected"}


class AngelOneBroker(Broker):
    def __init__(self, smart,
                 symbol_token_map: Dict[str, tuple[str, str]],
                 cost_model: Optional[IntradayEquityCostModel] = None):
        """
        smart            : authenticated SmartConnect instance (from angel_login())
        symbol_token_map : {symbol: (trading_symbol, token)}
                           e.g. {"RELIANCE": ("RELIANCE-EQ", "2885")}
                           trading_symbol is the NSE scrip name as in the instrument master.
        """
        self.smart = smart
        self.symbol_token_map = symbol_token_map
        self.cost_model = cost_model or IntradayEquityCostModel()
        self.resting: Dict[int, Order] = {}
        self._angel_ids: Dict[int, str] = {}    # our order id → Angel order id
        self.last_price: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Broker interface
    # ------------------------------------------------------------------

    def submit(self, order: Order) -> int:
        trading_symbol, token = self.symbol_token_map[order.symbol]
        params = {
            "variety":         _VARIETY[order.order_type],
            "tradingsymbol":   trading_symbol,
            "symboltoken":     token,
            "transactiontype": order.side.value,
            "exchange":        "NSE",
            "ordertype":       _ORDER_TYPE[order.order_type],
            "producttype":     "INTRADAY",
            "duration":        "DAY",
            "quantity":        str(order.qty),
            "price":           str(order.limit_price) if order.order_type is OrderType.LIMIT else "0",
            "triggerprice":    str(order.stop_price)  if order.order_type is OrderType.STOP  else "0",
        }
        resp = self.smart.placeOrder(params)
        if not resp.get("status"):
            raise RuntimeError(f"placeOrder failed: {resp.get('message')} | params={params}")
        angel_id = resp["data"]["orderid"]
        order.status = OrderStatus.OPEN
        self.resting[order.id] = order
        self._angel_ids[order.id] = angel_id
        print(f"[AngelOneBroker] submitted {order.side.value} {order.qty} {order.symbol} "
              f"({order.order_type.value}) → angel_id={angel_id}")
        return order.id

    def cancel(self, order_id: int) -> bool:
        angel_id = self._angel_ids.get(order_id)
        if not angel_id:
            return False
        o = self.resting.get(order_id)
        variety = _VARIETY.get(o.order_type, "NORMAL") if o else "NORMAL"
        try:
            self.smart.cancelOrder(angel_id, variety)
        except Exception as exc:
            print(f"[AngelOneBroker] cancelOrder {angel_id} failed: {exc}")
            return False
        o = self.resting.pop(order_id, None)
        if o:
            o.status = OrderStatus.CANCELLED
        return True

    def on_bar(self, bar: Bar) -> List[Fill]:
        self.last_price[bar.symbol] = bar.close
        if not self.resting:
            return []

        fills: List[Fill] = []
        try:
            resp = self.smart.orderBook()
            if not resp.get("status") or not resp.get("data"):
                return []
            book: Dict[str, dict] = {e["orderid"]: e for e in resp["data"]}

            for our_id in list(self.resting.keys()):
                angel_id = self._angel_ids.get(our_id)
                if not angel_id or angel_id not in book:
                    continue
                entry  = book[angel_id]
                status = entry.get("status", "").lower()

                if status == _ANGEL_FILLED:
                    o = self.resting.pop(our_id)
                    fill_price = float(entry.get("averageprice") or bar.close)
                    fill_qty   = int(entry.get("filledshares") or o.qty)
                    fills.append(self._make_fill(o, our_id, fill_qty, fill_price, bar.ts))

                elif status in _ANGEL_CANCELLED:
                    o = self.resting.pop(our_id, None)
                    if o:
                        o.status = OrderStatus.CANCELLED
                    print(f"[AngelOneBroker] order {angel_id} {status}: "
                          f"{entry.get('text', '')}")
        except Exception as exc:
            print(f"[AngelOneBroker] orderBook poll error: {exc}")

        return fills

    def force_liquidate(self, symbol: str, signed_qty: int,
                        price: float, ts: datetime) -> Optional[Fill]:
        """Square-off: place a real market order and poll until confirmed."""
        if signed_qty == 0:
            return None
        side = Side.SELL if signed_qty > 0 else Side.BUY
        order = Order(symbol=symbol, side=side, qty=abs(signed_qty),
                      order_type=OrderType.MARKET, tag="squareoff")
        order.status = OrderStatus.OPEN
        self.submit(order)
        angel_id = self._angel_ids[order.id]

        # Poll up to ~10 seconds for the fill confirmation
        for _ in range(10):
            _time.sleep(1)
            try:
                resp = self.smart.orderBook()
                if resp.get("status") and resp.get("data"):
                    for entry in resp["data"]:
                        if (entry["orderid"] == angel_id
                                and entry.get("status", "").lower() == _ANGEL_FILLED):
                            fill_price = float(entry.get("averageprice") or price)
                            self.resting.pop(order.id, None)
                            return self._make_fill(order, order.id,
                                                   abs(signed_qty), fill_price, ts)
            except Exception as exc:
                print(f"[AngelOneBroker] squareoff poll error: {exc}")

        # Fallback — order placed but fill unconfirmed; use last known price
        print(f"[AngelOneBroker] squareoff fill unconfirmed — using last price {price:.2f}")
        self.resting.pop(order.id, None)
        return self._make_fill(order, order.id, abs(signed_qty), price, ts)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _make_fill(self, order: Order, our_id: int,
                   fill_qty: int, fill_price: float, ts: datetime) -> Fill:
        charges = self.cost_model.charges(order.side, fill_price, fill_qty)
        order.status = OrderStatus.FILLED
        order.filled_qty = fill_qty
        order.avg_fill_price = fill_price
        order.ts_updated = ts
        print(f"[AngelOneBroker] fill  {order.side.value} {fill_qty} {order.symbol} "
              f"@ {fill_price:.2f}  charges={charges:.2f}  tag={order.tag}")
        return Fill(order_id=our_id, symbol=order.symbol, side=order.side,
                    qty=fill_qty, price=round(fill_price, 2), ts=ts,
                    charges=round(charges, 2), tag=order.tag)
