import time
from dataclasses import dataclass
from threading import Event
from typing import Any

import ntplib
import settrade_v2.context as settrade_context
from settrade_v2 import Investor
from settrade_v2.errors import SettradeError

from .settings import ConfigError, Settings


def _use_network_clock() -> None:
    """Settrade rejects login when the machine clock is more than a few seconds off."""
    if getattr(settrade_context, "_network_clock", False):
        return
    offset_ms = 0
    try:
        packet = ntplib.NTPClient().request("2.asia.pool.ntp.org", version=3)
        offset_ms = int(packet.tx_time * 1000) - int(time.time() * 1000)
    except OSError:
        offset_ms = 0

    def timestamp() -> str:
        return str(int(time.time() * 1000) + offset_ms)

    settrade_context.get_current_milli_timestamp_str = timestamp
    settrade_context._network_clock = True


def _skip_broken_version_check() -> None:
    """The SDK crashes when the version URL returns an empty body."""
    if getattr(settrade_context.Context, "_version_guard", False):
        return

    original = settrade_context.Context._check_version

    def _check_version(self) -> None:
        try:
            original(self)
        except Exception as exc:
            print(
                f"[settrade] skipped SDK version check: {type(exc).__name__}: {exc}",
                flush=True,
            )

    settrade_context.Context._check_version = _check_version
    settrade_context.Context._version_guard = True


@dataclass
class ApiResult:
    method: str
    endpoint: str
    data: Any


def _money(value: Any) -> Any:
    if isinstance(value, dict) and set(value) <= {"units", "nanos"} and (
        "units" in value or "nanos" in value
    ):
        units = float(value.get("units") or 0)
        nanos = float(value.get("nanos") or 0) / 1_000_000_000
        return units + nanos
    return value


_CANDLE_KEYS = {
    "t": "time",
    "time": "time",
    "timestamp": "time",
    "datetime": "time",
    "o": "open",
    "open": "open",
    "h": "high",
    "high": "high",
    "l": "low",
    "low": "low",
    "c": "close",
    "close": "close",
    "last": "last",
    "v": "volume",
    "volume": "volume",
    "total_volume": "volume",
    "totalVolume": "volume",
    "value": "total_value",
    "total_value": "total_value",
    "totalValue": "total_value",
}


def _merge_live_bar(bars: list, live: dict[str, Any]) -> list:
    if bars and isinstance(bars[-1], dict) and bars[-1].get("time") == live.get("time"):
        merged = {**bars[-1], **{k: v for k, v in live.items() if v is not None}}
        merged.setdefault("volume", None)
        merged.setdefault("total_value", None)
        return [*bars[:-1], merged]
    return [*bars, live]


def _named_bar(bar: dict[str, Any]) -> dict[str, Any]:
    named = {_CANDLE_KEYS.get(key, key): value for key, value in bar.items()}
    named.setdefault("volume", None)
    named.setdefault("total_value", None)
    return named


def _named_candles(data: Any) -> Any:
    """Turn parallel candle arrays into one object per bar."""
    if isinstance(data, dict) and isinstance(data.get("data"), (dict, list)):
        inner = _named_candles(data["data"])
        return {**data, "data": inner}
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return [_named_bar(bar) for bar in data]
        return data
    if not isinstance(data, dict):
        return data
    columns: dict[str, list] = {}
    extra: dict[str, Any] = {}
    for key, value in data.items():
        name = _CANDLE_KEYS.get(key, key)
        if isinstance(value, list):
            columns[name] = value
        else:
            extra[key] = value
    if not columns:
        return data
    length = max(len(values) for values in columns.values())
    bars = []
    for index in range(length):
        bar = {
            name: values[index] if index < len(values) else None
            for name, values in columns.items()
        }
        bar.setdefault("volume", None)
        bar.setdefault("total_value", None)
        bars.append(bar)
    if extra:
        return {"bars": bars, **extra}
    return bars


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quantity_000_shares(volume: Any) -> float | None:
    """SET table shows quantity in thousands of shares."""
    number = _number(volume)
    if number is None:
        return None
    if abs(number) >= 1_000_000_000:
        return round(number / 1_000, 2)
    return number


def _value_million_baht(value: Any) -> float | None:
    """SET table shows value in million baht."""
    number = _number(value)
    if number is None:
        return None
    if abs(number) >= 1_000_000_000:
        return round(number / 1_000_000, 2)
    return round(number, 2)


def _pick(data: dict[str, Any], *names: str) -> Any:
    for name in names:
        if data.get(name) not in (None, ""):
            return data[name]
    return None


def _flatten(value: Any) -> Any:
    if isinstance(value, dict):
        amount = _money(value)
        if amount is not value:
            return amount
        return {key: _flatten(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_flatten(item) for item in value]
    return value


def _call(action) -> dict[str, Any]:
    try:
        result = action()
    except (SettradeError, ConfigError) as exc:
        return {"error": str(exc)}
    return {
        "method": result.method,
        "endpoint": result.endpoint,
        "data": result.data,
    }


class SettradeClient:
    """Logged-in investor client. Read methods only."""

    def __init__(self, settings: Settings, is_auto_queue: bool = False):
        settings.require_credentials()
        self.settings = settings
        _use_network_clock()
        _skip_broken_version_check()
        try:
            self.investor = Investor(
                app_id=settings.app_id,
                app_secret=settings.app_secret,
                app_code=settings.app_code,
                broker_id=settings.broker_id,
                is_auto_queue=is_auto_queue,
            )
        except SettradeError as exc:
            raise ConfigError(f"Settrade login failed: {exc}") from exc
        except Exception as exc:
            raise ConfigError(
                f"Settrade login crashed: {type(exc).__name__}: {exc}"
            ) from exc

        self.market = self.investor.MarketData()
        self._equity = None
        self._derivatives = None

    @property
    def broker_id(self) -> str:
        return self.investor._ctx.broker_id

    @property
    def open_api_url(self) -> str:
        return self.investor._ctx.base_url

    @property
    def market_url(self) -> str:
        return self.market.market_url

    def quote(self, symbol: str) -> ApiResult:
        symbol = symbol.strip().upper()
        endpoint = f"{self.market_url}/quote/{symbol}"
        data = self.market.get_quote_symbol(symbol)
        if isinstance(data, dict):
            live = self._live_price_fields(symbol)
            for key, value in live.items():
                if value is None and data.get(key) not in (None, ""):
                    continue
                data[key] = value
            data["volume"] = _pick(
                data, "volume", "total_volume", "totalVolume", "total_index_volume"
            )
            data["total_value"] = _pick(
                data, "total_value", "totalValue", "total_index_value", "value"
            )
            data.setdefault("last", None)
            data.setdefault("volume", None)
            data.setdefault("total_value", None)
        return ApiResult("GET", endpoint, data)

    def price_info(self, symbol: str, timeout: float = 8) -> ApiResult:
        symbol = symbol.strip().upper()
        fields = self._live_price_fields(symbol, timeout=timeout)
        return ApiResult("MQTT", f"proto/topic/infov3/{symbol}", fields)

    def set_index(self, market: str = "SET", timeout: float = 8) -> ApiResult:
        market = market.strip().upper() or "SET"
        try:
            realtime = self.investor.RealtimeDataConnection()
            message = self._listen_once(
                lambda on_message: realtime.subscribe_equity_exchange_info(
                    market, on_message
                ),
                timeout=timeout,
            )
        except (SettradeError, ConfigError, OSError) as exc:
            message = {"is_success": False, "message": str(exc)}
        return ApiResult(
            "MQTT",
            f"proto/topic/exchinfoeqv3/{market}",
            self._set_fields(message),
        )

    def _set_fields(self, message: Any) -> dict[str, Any]:
        if not isinstance(message, dict) or not message.get("is_success"):
            detail = message.get("message") if isinstance(message, dict) else message
            return {
                "index": "SET",
                "latest": None,
                "last": None,
                "change": None,
                "volume": None,
                "total_value": None,
                "realtime_error": detail or "SET index was not received.",
            }
        payload = _flatten(message.get("data") or {})
        info = payload.get("market_info") if isinstance(payload, dict) else None
        if not isinstance(info, dict):
            info = payload if isinstance(payload, dict) else {}
        fields = dict(info)
        latest = _pick(fields, "latest", "last", "current")
        change = _pick(fields, "change")
        volume = _quantity_000_shares(
            _pick(fields, "volume", "total_index_volume", "total_volume", "totalVolume")
        )
        total_value = _value_million_baht(
            _pick(fields, "total_value", "total_index_value", "totalValue", "value")
        )
        fields["index"] = fields.get("name") or "SET"
        fields["latest"] = latest
        fields["last"] = latest
        fields["change"] = change
        fields["volume"] = volume
        fields["total_value"] = total_value
        return fields

    def _live_price_fields(self, symbol: str, timeout: float = 8) -> dict[str, Any]:
        try:
            realtime = self.investor.RealtimeDataConnection()
            message = self._listen_once(
                lambda on_message: realtime.subscribe_price_info(symbol, on_message),
                timeout=timeout,
            )
        except (SettradeError, ConfigError, OSError) as exc:
            return {"last": None, "total_value": None, "realtime_error": str(exc)}
        if not isinstance(message, dict) or not message.get("is_success"):
            detail = message.get("message") if isinstance(message, dict) else message
            return {
                "last": None,
                "total_value": None,
                "realtime_error": detail or "Realtime price was not received.",
            }
        payload = _flatten(message.get("data") or {})
        if not isinstance(payload, dict):
            return {"last": None, "total_value": None, "realtime_error": payload}
        payload.setdefault("last", None)
        payload.setdefault("total_value", None)
        return payload

    def _live_candle(self, symbol: str, interval: str, timeout: float = 8) -> dict[str, Any] | None:
        try:
            realtime = self.investor.RealtimeDataConnection()
            message = self._listen_once(
                lambda on_message: realtime.subscribe_candlestick(
                    symbol, interval, on_message
                ),
                timeout=timeout,
            )
        except (SettradeError, ConfigError, OSError):
            return None
        if not isinstance(message, dict) or not message.get("is_success"):
            return None
        payload = _flatten(message.get("data") or {})
        if not isinstance(payload, dict):
            return None
        bar = _named_bar(payload)
        bar["volume"] = _pick(bar, "volume", "total_volume", "totalVolume")
        bar["total_value"] = _pick(bar, "total_value", "value", "totalValue")
        return bar

    def _listen_once(self, subscribe, timeout: float) -> dict[str, Any]:
        done = Event()
        box: dict[str, Any] = {}

        def on_message(message: dict[str, Any]) -> None:
            if done.is_set():
                return
            box["message"] = message
            done.set()

        subscriber = subscribe(on_message)
        subscriber.start()
        try:
            if not done.wait(timeout):
                return {
                    "is_success": False,
                    "message": "No realtime message arrived before the timeout.",
                }
            return box["message"]
        finally:
            subscriber.stop()

    def candlesticks(
        self,
        symbol: str,
        interval: str = "1d",
        limit: int = 10,
        start: str | None = None,
        end: str | None = None,
        normalized: bool = True,
    ) -> ApiResult:
        symbol = symbol.strip().upper()
        endpoint = (
            f"{self.market_url}/candlesticks".replace("marketdata", "techchart", 1)
            + f"?symbol={symbol}&interval={interval}&limit={limit}"
        )
        data = self.market.get_candlestick(
            symbol=symbol,
            interval=interval,
            limit=limit,
            start=start,
            end=end,
            normalized=normalized,
        )
        bars = _named_candles(data)
        if isinstance(bars, dict) and isinstance(bars.get("bars"), list):
            bars = bars["bars"]
        if not isinstance(bars, list):
            bars = []
        live = self._live_candle(symbol, interval)
        if live is not None:
            bars = _merge_live_bar(bars, live)
        return ApiResult("GET", endpoint, bars)

    def equity_account(self) -> ApiResult:
        equity = self._equity_client()
        endpoint = f"{equity.base_url}/{equity._account_no}/account-info"
        return ApiResult("GET", endpoint, equity.get_account_info())

    def equity_orders(self) -> ApiResult:
        equity = self._equity_client()
        endpoint = f"{equity.base_url}/{equity._account_no}/orders"
        return ApiResult("GET", endpoint, equity.get_orders())

    def equity_portfolios(self) -> ApiResult:
        equity = self._equity_client()
        endpoint = f"{equity.base_url}/{equity._account_no}/portfolios"
        return ApiResult("GET", endpoint, equity.get_portfolios())

    def equity_trades(self) -> ApiResult:
        equity = self._equity_client()
        endpoint = (
            f"{self.open_api_url}/api/seos/v4/{self.broker_id}"
            f"/accounts/{equity._account_no}/trades"
        )
        return ApiResult("GET", endpoint, equity.get_trades())

    def derivatives_account(self) -> ApiResult:
        deriv = self._derivatives_client()
        endpoint = f"{deriv.base_url}/{deriv._account_no}/account-info"
        return ApiResult("GET", endpoint, deriv.get_account_info())

    def derivatives_orders(self) -> ApiResult:
        deriv = self._derivatives_client()
        endpoint = f"{deriv.base_url}/{deriv._account_no}/orders"
        return ApiResult("GET", endpoint, deriv.get_orders())

    def fetch_all(
        self,
        symbol: str = "PTT",
        interval: str = "1d",
        limit: int = 10,
        on_result=None,
    ) -> dict[str, Any]:
        """Call every read API and keep each raw response."""
        symbol = symbol.strip().upper() or "PTT"
        calls = {
            "quote": lambda: self.quote(symbol),
            "candles": lambda: self.candlesticks(
                symbol, interval=interval, limit=limit
            ),
            "account": self.equity_account,
            "orders": self.equity_orders,
            "portfolios": self.equity_portfolios,
            "trades": self.equity_trades,
            "derivatives_account": self.derivatives_account,
            "derivatives_orders": self.derivatives_orders,
            "derivatives_portfolios": self.derivatives_portfolios,
        }
        calls["set"] = lambda: self.set_index()
        bodies: dict[str, Any] = {}
        for name, action in calls.items():
            item = _call(action)
            if on_result is not None:
                on_result(name, item)
            bodies[name] = item.get("data", item)
        market = bodies.get("set") if isinstance(bodies.get("set"), dict) else {}
        shown = ("index", "latest", "last", "change", "volume", "total_value")
        set_rest = {key: value for key, value in market.items() if key not in shown}
        bodies.pop("set", None)
        set_row = {
            "index": market.get("index") or "SET",
            "latest": market.get("latest", market.get("last")),
            "last": market.get("last", market.get("latest")),
            "change": market.get("change"),
            "volume": market.get("volume"),
            "total_value": market.get("total_value"),
            **set_rest,
        }
        return {
            "set": set_row,
            "index": set_row["index"],
            "latest": set_row["latest"],
            "change": set_row["change"],
            "volume": set_row["volume"],
            "total_value": set_row["total_value"],
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
            **bodies,
        }

    def derivatives_portfolios(self) -> ApiResult:
        deriv = self._derivatives_client()
        endpoint = f"{deriv.base_url}/{deriv._account_no}/portfolios"
        return ApiResult("GET", endpoint, deriv.get_portfolios())

    def _equity_client(self):
        if not self.settings.account_no:
            raise ConfigError("Set SETTRADE_ACCOUNT_NO in .env for equity calls.")
        if self._equity is None:
            self._equity = self.investor.Equity(account_no=self.settings.account_no)
        return self._equity

    def _derivatives_client(self):
        if not self.settings.deriv_account_no:
            raise ConfigError(
                "Set SETTRADE_DERIV_ACCOUNT_NO in .env for derivatives calls."
            )
        if self._derivatives is None:
            self._derivatives = self.investor.Derivatives(
                account_no=self.settings.deriv_account_no
            )
        return self._derivatives
