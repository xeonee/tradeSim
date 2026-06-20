"""Indian intraday equity cost model.

Rates are for EQUITY INTRADAY (MIS) on NSE, verified against publicly published
broker charge sheets (e.g. zerodha.com/charges) in 2026. The statutory pieces
(STT, stamp duty, exchange transaction charges, SEBI fee, GST) are set by the
government/exchanges and change periodically — treat these as defaults and
re-verify before trusting the numbers. Rates are fractions (0.0003 == 0.03%).

Note: DP charges do NOT apply to intraday (nothing is debited from demat), so
they are intentionally absent. IPFT is negligible and omitted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import Side


@dataclass
class CostConfig:
    brokerage_pct: float = 0.0003     # 0.03% of turnover ...
    brokerage_cap: float = 20.0       # ... or flat Rs 20 per executed order, whichever is lower
    stt_sell_pct: float = 0.00025     # STT 0.025% on SELL side only (intraday)
    txn_pct: float = 0.0000297        # NSE exchange transaction charge ~0.00297% (both sides)
    sebi_pct: float = 0.000001        # SEBI fee: Rs 10 per crore == 0.0001% (both sides)
    stamp_buy_pct: float = 0.00003    # stamp duty 0.003% on BUY side only (intraday)
    gst_pct: float = 0.18             # 18% GST on (brokerage + txn + sebi)


class IntradayEquityCostModel:
    def __init__(self, config: Optional[CostConfig] = None):
        self.cfg = config or CostConfig()

    def brokerage(self, turnover: float) -> float:
        return min(self.cfg.brokerage_pct * turnover, self.cfg.brokerage_cap)

    def charges(self, side: Side, price: float, qty: int) -> float:
        """Total brokerage + statutory charges for a single fill."""
        turnover = price * qty
        c = self.cfg
        brokerage = self.brokerage(turnover)
        txn = c.txn_pct * turnover
        sebi = c.sebi_pct * turnover
        stt = c.stt_sell_pct * turnover if side is Side.SELL else 0.0
        stamp = c.stamp_buy_pct * turnover if side is Side.BUY else 0.0
        gst = c.gst_pct * (brokerage + txn + sebi)
        return brokerage + txn + sebi + stt + stamp + gst
