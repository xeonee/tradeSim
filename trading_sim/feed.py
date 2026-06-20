"""Market-data feeds.

The engine only depends on the MarketDataFeed interface (an iterable of Bars in
time order). v1 ships a synthetic random-walk feed so the whole system runs with
zero setup, plus a CSV feed so you can drop in real historical data. A live
websocket feed (Upstox / Zerodha) would be a third implementation of this same
interface and nothing else in the system would change.
"""
from __future__ import annotations

import csv
import random
from datetime import date, datetime, time, timedelta
from typing import Iterator, Optional

from .models import Bar


class MarketDataFeed:
    def __iter__(self) -> Iterator[Bar]:
        raise NotImplementedError


class SyntheticIntradayFeed(MarketDataFeed):
    """1-minute OHLCV bars for one symbol across N trading days.

    Prices follow a Gaussian random walk within each session. This is
    intentionally edge-free: on pure noise, after costs, a crossover strategy
    should roughly break even or lose — exactly the honest baseline you want a
    simulator to show.
    """

    def __init__(self, symbol: str = "ACME", days: int = 3, start_date: Optional[date] = None,
                 session_start: time = time(9, 15), session_end: time = time(15, 30),
                 start_price: float = 2500.0, per_min_vol: float = 0.0008, seed: int = 42):
        self.symbol = symbol
        self.days = days
        self.start_date = start_date or date(2026, 6, 1)
        self.session_start = session_start
        self.session_end = session_end
        self.start_price = start_price
        self.per_min_vol = per_min_vol
        self.seed = seed

    def __iter__(self) -> Iterator[Bar]:
        rng = random.Random(self.seed)
        price = self.start_price
        d = self.start_date
        days_done = 0
        while days_done < self.days:
            if d.weekday() >= 5:  # skip weekends
                d += timedelta(days=1)
                continue
            t = datetime.combine(d, self.session_start)
            end = datetime.combine(d, self.session_end)
            while t <= end:
                open_ = price
                close = max(0.01, open_ + rng.gauss(0, self.per_min_vol) * price)
                high = max(open_, close) * (1 + abs(rng.gauss(0, self.per_min_vol)) * 0.5)
                low = min(open_, close) * (1 - abs(rng.gauss(0, self.per_min_vol)) * 0.5)
                yield Bar(ts=t, symbol=self.symbol, open=round(open_, 2),
                          high=round(high, 2), low=round(low, 2),
                          close=round(close, 2), volume=rng.randint(500, 5000))
                price = close
                t += timedelta(minutes=1)
            days_done += 1
            d += timedelta(days=1)


class CsvBarFeed(MarketDataFeed):
    """Bars from a CSV with header: ts,symbol,open,high,low,close,volume
    where ts is ISO-8601 (e.g. 2026-06-01T09:15:00). Rows must be time-ordered.
    """

    def __init__(self, path: str):
        self.path = path

    def __iter__(self) -> Iterator[Bar]:
        with open(self.path, newline="") as f:
            for row in csv.DictReader(f):
                yield Bar(
                    ts=datetime.fromisoformat(row["ts"]),
                    symbol=row["symbol"],
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=float(row.get("volume", 0) or 0),
                )
