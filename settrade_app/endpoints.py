"""REST paths used by settrade-v2 2.2.1.

Templates use {broker} and {account}. The SDK fills those after login.
Sandbox (broker id SANDBOX) switches the hosts to the UAT base URLs and
uses broker id 098.
"""

from dataclasses import dataclass


OPEN_API_PROD = "https://open-api.settrade.com"
OPEN_API_UAT = "https://open-api-test.settrade.com"
MARKET_PROD = "https://marketapi.settrade.com"
MARKET_UAT = "https://marketapi-test.settrade.com"


@dataclass(frozen=True)
class Endpoint:
    group: str
    method: str
    path: str
    sdk: str
    description: str


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint(
        "auth",
        "POST",
        "{open_api}/api/oam/v1/{broker}/broker-apps/{app_code}/login",
        "Investor()",
        "Sign in with the app id, timestamp, and ECDSA signature.",
    ),
    Endpoint(
        "auth",
        "POST",
        "{open_api}/api/oam/v1/{broker}/broker-apps/{app_code}/refresh-token",
        "Investor()",
        "Refresh the access token before it expires.",
    ),
    Endpoint(
        "market",
        "GET",
        "{market}/api/marketdata/v3/{broker}/quote/{symbol}",
        "MarketData.get_quote_symbol",
        "Last quote for one equity or derivatives symbol.",
    ),
    Endpoint(
        "market",
        "GET",
        "{market}/api/techchart/v3/{broker}/candlesticks",
        "MarketData.get_candlestick",
        "OHLCV bars. Query: symbol, interval, limit, start, end, normalized.",
    ),
    Endpoint(
        "equity",
        "GET",
        "{open_api}/api/seos/v3/{broker}/accounts/{account}/account-info",
        "Equity.get_account_info",
        "Equity cash, credit, and buying power.",
    ),
    Endpoint(
        "equity",
        "GET",
        "{open_api}/api/seos/v3/{broker}/accounts/{account}/orders",
        "Equity.get_orders",
        "Open and recent equity orders.",
    ),
    Endpoint(
        "equity",
        "GET",
        "{open_api}/api/seos/v3/{broker}/accounts/{account}/orders/{order_no}",
        "Equity.get_order",
        "One equity order by order number.",
    ),
    Endpoint(
        "equity",
        "GET",
        "{open_api}/api/seos/v3/{broker}/accounts/{account}/portfolios",
        "Equity.get_portfolios",
        "Equity holdings.",
    ),
    Endpoint(
        "equity",
        "GET",
        "{open_api}/api/seos/v4/{broker}/accounts/{account}/trades",
        "Equity.get_trades",
        "Equity executions for the session.",
    ),
    Endpoint(
        "derivatives",
        "GET",
        "{open_api}/api/seosd/v3/{broker}/accounts/{account}/account-info",
        "Derivatives.get_account_info",
        "Derivatives account balance and margin.",
    ),
    Endpoint(
        "derivatives",
        "GET",
        "{open_api}/api/seosd/v3/{broker}/accounts/{account}/orders",
        "Derivatives.get_orders",
        "Derivatives orders.",
    ),
    Endpoint(
        "derivatives",
        "GET",
        "{open_api}/api/seosd/v3/{broker}/accounts/{account}/portfolios",
        "Derivatives.get_portfolios",
        "Open derivatives positions.",
    ),
    Endpoint(
        "derivatives",
        "GET",
        "{open_api}/api/seosd/v3/{broker}/accounts/{account}/trades",
        "Derivatives.get_trades",
        "Derivatives executions.",
    ),
    Endpoint(
        "realtime",
        "GET",
        "{open_api}/api/dispatcher/v3/{broker}/token",
        "RealtimeDataConnection",
        "WebSocket host and token for MQTT market data.",
    ),
)


def hosts(sandbox: bool) -> dict[str, str]:
    if sandbox:
        return {"open_api": OPEN_API_UAT, "market": MARKET_UAT}
    return {"open_api": OPEN_API_PROD, "market": MARKET_PROD}
