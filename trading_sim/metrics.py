"""Performance reporting from the portfolio's equity curve and fills."""
from __future__ import annotations

import math
from typing import Dict

from .portfolio import Portfolio

# 1-min bars: ~375 per NSE session, ~252 sessions/year (rough, annualisation only)
_BARS_PER_YEAR = 375 * 252


def performance_report(pf: Portfolio) -> Dict[str, float]:
    eq = [e for _, e in pf.equity_curve]
    start = pf.starting_cash
    final = eq[-1] if eq else start
    net_pnl = final - start

    peak = -math.inf
    max_dd = 0.0
    for v in eq:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)

    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1] > 0]
    sharpe = 0.0
    if len(rets) > 1:
        m = sum(rets) / len(rets)
        var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
        sd = math.sqrt(var)
        if sd > 0:
            sharpe = (m / sd) * math.sqrt(_BARS_PER_YEAR)

    wins = [p for p in pf.trade_pnls if p > 0]
    losses = [p for p in pf.trade_pnls if p < 0]
    n = len(pf.trade_pnls)
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    return {
        "starting_cash": start,
        "final_equity": final,
        "net_pnl": net_pnl,
        "net_return_pct": 100 * net_pnl / start if start else 0.0,
        "total_charges": pf.total_charges,
        "gross_pnl_before_costs": net_pnl + pf.total_charges,
        "num_fills": float(len(pf.fills)),
        "num_round_trips": float(n),
        "win_rate_pct": 100 * len(wins) / n if n else 0.0,
        "avg_win": gross_profit / len(wins) if wins else 0.0,
        "avg_loss": -gross_loss / len(losses) if losses else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
        "max_drawdown_pct": 100 * max_dd,
        "sharpe_annualised": sharpe,
    }


def format_report(r: Dict[str, float]) -> str:
    return "\n".join([
        "==================== SIMULATION RESULT ====================",
        f"  Starting cash         : Rs {r['starting_cash']:,.2f}",
        f"  Final equity          : Rs {r['final_equity']:,.2f}",
        f"  Net P&L (after costs) : Rs {r['net_pnl']:,.2f}  ({r['net_return_pct']:+.3f}%)",
        f"  Gross P&L (pre-costs) : Rs {r['gross_pnl_before_costs']:,.2f}",
        f"  Total charges paid    : Rs {r['total_charges']:,.2f}",
        "  ----------------------------------------------------------",
        f"  Fills                 : {int(r['num_fills'])}",
        f"  Round-trip trades     : {int(r['num_round_trips'])}",
        f"  Win rate              : {r['win_rate_pct']:.1f}%",
        f"  Avg win / Avg loss    : Rs {r['avg_win']:,.2f} / Rs {r['avg_loss']:,.2f}",
        f"  Profit factor         : {r['profit_factor']:.2f}",
        f"  Max drawdown          : {r['max_drawdown_pct']:.2f}%",
        f"  Sharpe (annualised)   : {r['sharpe_annualised']:.2f}   (rough; bar-level, no rf)",
        "===========================================================",
    ])
