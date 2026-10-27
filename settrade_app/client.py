import time
from dataclasses import dataclass
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


@dataclass
class ApiResult:
    method: str
    endpoint: str
    data: Any


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
        return ApiResult("GET", endpoint, self.market.get_quote_symbol(symbol))

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
        return ApiResult("GET", endpoint, data)

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
        apis: dict[str, Any] = {}
        for name, action in calls.items():
            item = _call(action)
            apis[name] = item
            if on_result is not None:
                on_result(name, item)
        return {
            "open_api": self.open_api_url,
            "market": self.market_url,
            "broker_id": self.broker_id,
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
            "apis": apis,
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
