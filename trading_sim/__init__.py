"""A minimal, broker-agnostic intraday equity trading simulator (paper trading).

Public API:
    from trading_sim import (
        SyntheticIntradayFeed, CsvBarFeed,
        SimulatedBroker, Portfolio,
        Strategy, Context, SmaCrossStrategy,
        SimulationEngine,
        IntradayEquityCostModel, CostConfig,
    )
"""
from .broker import Broker, SimulatedBroker
from .costs import CostConfig, IntradayEquityCostModel
from .engine import SimulationEngine
from .feed import CsvBarFeed, MarketDataFeed, SyntheticIntradayFeed
from .models import Bar, Fill, Order, OrderStatus, OrderType, Position, Side
from .portfolio import Portfolio
from .sizer import PositionSizer, SizingMethod
from .strategy import Context, SmaCrossStrategy, Strategy

__all__ = [
    "Side", "OrderType", "OrderStatus", "Bar", "Order", "Fill", "Position",
    "CostConfig", "IntradayEquityCostModel",
    "MarketDataFeed", "SyntheticIntradayFeed", "CsvBarFeed",
    "Broker", "SimulatedBroker", "Portfolio",
    "Strategy", "Context", "SmaCrossStrategy", "SimulationEngine",
    "PositionSizer", "SizingMethod",
]
