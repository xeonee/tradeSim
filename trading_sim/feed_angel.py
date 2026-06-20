"""Live 1-minute bar feed from Angel One SmartAPI websocket.

Ticks arrive asynchronously via callbacks. This class aggregates them into
completed 1-minute OHLCV bars and yields each bar through a thread-safe queue
so the synchronous engine loop can consume them without change.

Required pip packages (outside the stdlib-only core):
    pip install smartapi-python pyotp

Credentials are passed in at construction — read them from environment
variables in the calling script, not hardcoded here.

Usage:
    feed = AngelOneFeed(
        symbol="RELIANCE",
        exchange_token="2885",          # look up with lookup_token.py
        api_key=os.environ["ANGEL_API_KEY"],
        client_id=os.environ["ANGEL_CLIENT_ID"],
        password=os.environ["ANGEL_PASSWORD"],
        totp_secret=os.environ["ANGEL_TOTP_SECRET"],
    )
"""
from __future__ import annotations

import queue
import threading
from datetime import datetime, time
from typing import Dict, Iterator, Optional

from .feed import MarketDataFeed
from .models import Bar


def angel_login(api_key: str, client_id: str, password: str, totp_secret: str):
    """Authenticate with Angel One SmartAPI once.

    Returns (smart, auth_token, feed_token). Pass these into both
    AngelOneFeed and AngelOneBroker so only one session is created.
    """
    try:
        import pyotp
        from SmartApi import SmartConnect
    except ImportError:
        raise ImportError("Missing dependencies. Run:  pip install smartapi-python pyotp")

    totp = pyotp.TOTP(totp_secret).now()
    smart = SmartConnect(api_key=api_key)
    resp = smart.generateSession(client_id, password, totp)
    if not resp.get("status"):
        raise RuntimeError(f"Angel One login failed: {resp.get('message')}")
    auth_token = resp["data"]["jwtToken"]
    feed_token = smart.getfeedToken()
    return smart, auth_token, feed_token


class AngelOneFeed(MarketDataFeed):
    _EXCHANGE_TYPE = 1       # 1 = NSE Cash segment
    _SUBSCRIBE_MODE = 2      # 2 = Quote (LTP + volume + day OHLC)

    def __init__(self, symbol: str, exchange_token: str,
                 api_key: str, client_id: str, password: str, totp_secret: str,
                 # pre-authenticated session — pass these to avoid a second login
                 auth_token: Optional[str] = None,
                 feed_token: Optional[str] = None):
        self.symbol = symbol
        self.exchange_token = str(exchange_token)
        self.api_key = api_key
        self.client_id = client_id
        self.password = password
        self.totp_secret = totp_secret
        self._auth_token = auth_token
        self._feed_token = feed_token

        self._bar_queue: queue.Queue = queue.Queue()
        self._current: Optional[dict] = None   # in-progress bar accumulator
        self._prev_day_vol: float = 0.0        # to compute per-tick volume delta

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _ensure_tokens(self) -> tuple[str, str]:
        if self._auth_token and self._feed_token:
            return self._auth_token, self._feed_token
        _, auth, feed = angel_login(
            self.api_key, self.client_id, self.password, self.totp_secret
        )
        return auth, feed

    # ------------------------------------------------------------------
    # Websocket callbacks
    # ------------------------------------------------------------------

    def _on_open(self, wsapp) -> None:
        token_list = [{"exchangeType": self._EXCHANGE_TYPE,
                       "tokens": [self.exchange_token]}]
        wsapp.subscribe("live", self._SUBSCRIBE_MODE, token_list)
        print(f"[AngelOneFeed] subscribed to {self.symbol} ({self.exchange_token})")

    def _on_data(self, wsapp, message: dict) -> None:
        try:
            epoch_ms = message.get("exchange_timestamp", 0)
            ts = datetime.fromtimestamp(epoch_ms / 1000)
            minute = ts.replace(second=0, microsecond=0)

            # Angel One sends prices in paise — convert to rupees
            ltp = message["last_traded_price"] / 100.0

            # Cumulative day volume → per-bar delta
            day_vol = float(message.get("volume_trade_for_the_day", 0))
            tick_vol = max(0.0, day_vol - self._prev_day_vol)
            self._prev_day_vol = day_vol

            if self._current is None:
                self._current = {
                    "minute": minute,
                    "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                    "volume": tick_vol,
                }
            elif minute == self._current["minute"]:
                self._current["high"] = max(self._current["high"], ltp)
                self._current["low"] = min(self._current["low"], ltp)
                self._current["close"] = ltp
                self._current["volume"] += tick_vol
            else:
                # minute boundary crossed — emit the completed bar
                c = self._current
                self._bar_queue.put(Bar(
                    ts=c["minute"], symbol=self.symbol,
                    open=round(c["open"], 2), high=round(c["high"], 2),
                    low=round(c["low"], 2),  close=round(c["close"], 2),
                    volume=c["volume"],
                ))
                self._current = {
                    "minute": minute,
                    "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                    "volume": tick_vol,
                }
        except Exception as exc:
            print(f"[AngelOneFeed] tick error: {exc}")

    def _on_error(self, wsapp, error) -> None:
        print(f"[AngelOneFeed] websocket error: {error}")
        self._bar_queue.put(None)   # sentinel → stop iteration

    def _on_close(self, wsapp) -> None:
        print("[AngelOneFeed] websocket closed")
        self._bar_queue.put(None)   # sentinel → stop iteration

    # ------------------------------------------------------------------
    # MarketDataFeed interface
    # ------------------------------------------------------------------

    def __iter__(self) -> Iterator[Bar]:
        auth_token, feed_token = self._ensure_tokens()
        print(f"[AngelOneFeed] connecting websocket …")

        from SmartApi.smartWebSocketV2 import SmartWebSocketV2
        sws = SmartWebSocketV2(
            auth_token=auth_token,
            api_key=self.api_key,
            client_code=self.client_id,
            feed_token=feed_token,
        )
        sws.on_open  = self._on_open
        sws.on_data  = self._on_data
        sws.on_error = self._on_error
        sws.on_close = self._on_close

        # Run the websocket in a background daemon thread so the main thread
        # can block on queue.get() and yield bars synchronously.
        threading.Thread(target=sws.connect, daemon=True).start()

        session_end = time(15, 30)

        while True:
            bar = self._bar_queue.get()   # blocks until a bar is ready
            if bar is None:               # sentinel from on_error / on_close
                break
            yield bar
            if bar.ts.time() >= session_end:
                break


class AngelOneMultiFeed(MarketDataFeed):
    """Live 1-minute bar feed for multiple symbols on a single Angel One websocket.

    Ticks from all subscribed symbols arrive on one connection. Each symbol has
    its own bar accumulator. Completed bars are yielded in arrival order so the
    engine's time-ordered loop stays correct.

    symbol_token_map : {"RELIANCE": "2885", "TCS": "11536", ...}
    """

    _EXCHANGE_TYPE  = 1   # NSE Cash
    _SUBSCRIBE_MODE = 2   # Quote mode

    def __init__(self, symbol_token_map: Dict[str, str],
                 api_key: str, client_id: str, password: str, totp_secret: str,
                 auth_token: Optional[str] = None,
                 feed_token: Optional[str] = None):
        self.symbol_token_map = symbol_token_map
        self._token_symbol: Dict[str, str] = {v: k for k, v in symbol_token_map.items()}
        self.api_key      = api_key
        self.client_id    = client_id
        self.password     = password
        self.totp_secret  = totp_secret
        self._auth_token  = auth_token
        self._feed_token  = feed_token

        self._bar_queue: queue.Queue = queue.Queue()
        self._currents: Dict[str, Optional[dict]] = {s: None for s in symbol_token_map}
        self._prev_vols: Dict[str, float]         = {s: 0.0  for s in symbol_token_map}

    def _ensure_tokens(self) -> tuple[str, str]:
        if self._auth_token and self._feed_token:
            return self._auth_token, self._feed_token
        _, auth, feed = angel_login(
            self.api_key, self.client_id, self.password, self.totp_secret
        )
        return auth, feed

    def _on_open(self, wsapp) -> None:
        token_list = [{"exchangeType": self._EXCHANGE_TYPE,
                       "tokens": list(self.symbol_token_map.values())}]
        wsapp.subscribe("live", self._SUBSCRIBE_MODE, token_list)
        print(f"[AngelOneMultiFeed] subscribed to {len(self.symbol_token_map)} symbols")

    def _on_data(self, wsapp, message: dict) -> None:
        try:
            token  = str(message.get("token", ""))
            symbol = self._token_symbol.get(token)
            if not symbol:
                return

            epoch_ms = message.get("exchange_timestamp", 0)
            ts       = datetime.fromtimestamp(epoch_ms / 1000)
            minute   = ts.replace(second=0, microsecond=0)
            ltp      = message["last_traded_price"] / 100.0

            day_vol  = float(message.get("volume_trade_for_the_day", 0))
            tick_vol = max(0.0, day_vol - self._prev_vols[symbol])
            self._prev_vols[symbol] = day_vol

            cur = self._currents[symbol]
            if cur is None:
                self._currents[symbol] = {
                    "minute": minute,
                    "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                    "volume": tick_vol,
                }
            elif minute == cur["minute"]:
                cur["high"]   = max(cur["high"], ltp)
                cur["low"]    = min(cur["low"],  ltp)
                cur["close"]  = ltp
                cur["volume"] += tick_vol
            else:
                self._bar_queue.put(Bar(
                    ts=cur["minute"], symbol=symbol,
                    open=round(cur["open"],  2), high=round(cur["high"],  2),
                    low=round(cur["low"],   2), close=round(cur["close"], 2),
                    volume=cur["volume"],
                ))
                self._currents[symbol] = {
                    "minute": minute,
                    "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                    "volume": tick_vol,
                }
        except Exception as exc:
            print(f"[AngelOneMultiFeed] tick error: {exc}")

    def _on_error(self, wsapp, error) -> None:
        print(f"[AngelOneMultiFeed] websocket error: {error}")
        self._bar_queue.put(None)

    def _on_close(self, wsapp) -> None:
        print("[AngelOneMultiFeed] websocket closed")
        self._bar_queue.put(None)

    def __iter__(self) -> Iterator[Bar]:
        auth_token, feed_token = self._ensure_tokens()
        print(f"[AngelOneMultiFeed] connecting websocket …")

        from SmartApi.smartWebSocketV2 import SmartWebSocketV2
        sws = SmartWebSocketV2(
            auth_token=auth_token,
            api_key=self.api_key,
            client_code=self.client_id,
            feed_token=feed_token,
        )
        sws.on_open  = self._on_open
        sws.on_data  = self._on_data
        sws.on_error = self._on_error
        sws.on_close = self._on_close

        threading.Thread(target=sws.connect, daemon=True).start()

        session_end = time(15, 30)
        active = set(self.symbol_token_map.keys())

        while active:
            bar = self._bar_queue.get()
            if bar is None:
                break
            yield bar
            if bar.ts.time() >= session_end:
                active.discard(bar.symbol)
