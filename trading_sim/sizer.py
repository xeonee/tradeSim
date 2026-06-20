"""Position sizing — how many shares to buy on each entry signal.

Three methods, all configured at construction time:

  equal_capital  — spend capital/num_symbols rupees per trade regardless of
                   volatility. Simple and fair across symbols by price.

  risk           — risk a fixed fraction of capital per trade. qty is chosen
                   so that if the stop-loss fires, the loss equals
                   capital * risk_per_trade. Requires a stop_pct.

  volatility     — like risk, but uses the symbol's recent realized volatility
                   instead of a fixed stop_pct. Automatically sizes down on
                   wild stocks and up on calm ones.

Usage:
    sizer = PositionSizer(method="risk", capital=1_000_000,
                          risk_per_trade=0.01, stop_pct=0.004)
    qty = sizer.qty(price=2800.0, stop_pct=0.004, recent_closes=[...])
"""
from __future__ import annotations

import math
from enum import Enum
from typing import List, Optional


class SizingMethod(Enum):
    EQUAL_CAPITAL = "equal_capital"
    RISK          = "risk"
    VOLATILITY    = "volatility"


class PositionSizer:
    def __init__(self,
                 method: str = "risk",
                 capital: float = 1_000_000.0,
                 num_symbols: int = 1,
                 risk_per_trade: float = 0.01,
                 stop_pct: float = 0.004,
                 vol_lookback: int = 20,
                 max_position_pct: float = 0.20):
        """
        method            : "equal_capital" | "risk" | "volatility"
        capital           : total portfolio capital in rupees
        num_symbols       : number of symbols in the universe (used by equal_capital)
        risk_per_trade    : fraction of capital to risk per trade (used by risk + volatility)
        stop_pct          : fallback stop distance as fraction of price (used by risk)
        vol_lookback      : number of recent bars used to estimate volatility
        max_position_pct  : hard cap — no single position may exceed this fraction
                            of capital (default 0.20 = 20%)
        """
        self.method           = SizingMethod(method)
        self.capital          = capital
        self.num_symbols      = max(1, num_symbols)
        self.risk_per_trade   = risk_per_trade
        self.stop_pct         = stop_pct
        self.vol_lookback     = vol_lookback
        self.max_position_pct = max_position_pct

    def qty(self, price: float,
            stop_pct: Optional[float] = None,
            recent_closes: Optional[List[float]] = None) -> int:
        """Return the number of whole shares to buy.

        price         : current bar's close (entry reference price)
        stop_pct      : override the instance stop_pct for this call
        recent_closes : list of recent close prices (needed for volatility method)
        """
        if price <= 0:
            return 1

        sp = stop_pct if stop_pct is not None else self.stop_pct
        max_shares = max(1, int(self.capital * self.max_position_pct / price))

        if self.method is SizingMethod.EQUAL_CAPITAL:
            budget = self.capital / self.num_symbols
            return min(max_shares, max(1, int(budget / price)))

        elif self.method is SizingMethod.RISK:
            risk_rs        = self.capital * self.risk_per_trade
            loss_per_share = price * sp
            if loss_per_share <= 0:
                return 1
            return min(max_shares, max(1, int(risk_rs / loss_per_share)))

        elif self.method is SizingMethod.VOLATILITY:
            vol = self._realized_vol(recent_closes or [])
            if vol <= 0:
                # no history yet — fall back to equal capital
                return min(max_shares, max(1, int(self.capital / self.num_symbols / price)))
            risk_rs        = self.capital * self.risk_per_trade
            loss_per_share = price * vol
            return min(max_shares, max(1, int(risk_rs / loss_per_share)))

        return 1

    def _realized_vol(self, closes: List[float]) -> float:
        """Per-bar realized volatility (std-dev of log returns) over recent closes."""
        n = min(len(closes), self.vol_lookback)
        if n < 2:
            return 0.0
        recent = closes[-n:]
        log_rets = [math.log(recent[i] / recent[i - 1])
                    for i in range(1, len(recent))
                    if recent[i - 1] > 0]
        if len(log_rets) < 1:
            return 0.0
        mean = sum(log_rets) / len(log_rets)
        variance = sum((r - mean) ** 2 for r in log_rets) / len(log_rets)
        return math.sqrt(variance)
