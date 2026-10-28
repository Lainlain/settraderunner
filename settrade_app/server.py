"""Local HTTP server. GET routes return Settrade API data."""

import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import parse_qs, urlparse

from settrade_v2.errors import SettradeError

from .client import SettradeClient, set_numbers
from .endpoints import ENDPOINTS, hosts
from .settings import ROOT, ConfigError, Settings

_client: SettradeClient | None = None
_lock = Lock()


def _log(message: str) -> None:
    print(message, flush=True)


def _log_settrade(name: str, body: dict) -> None:
    if body.get("error") and "data" not in body:
        _log(f"[settrade] {name} FAILED")
        _log(body["error"])
        return
    _log(f"[settrade] {name} {body.get('method', 'GET')} {body.get('endpoint', '')}")
    _log(json.dumps(body.get("data"), indent=2, default=str, ensure_ascii=False))


def get_client() -> SettradeClient:
    global _client
    with _lock:
        if _client is None:
            _log("[settrade] logging in")
            _client = SettradeClient(Settings.from_env(), is_auto_queue=True)
            _log(
                "[settrade] logged in "
                f"broker={_client.broker_id} "
                f"open_api={_client.open_api_url} "
                f"market={_client.market_url}"
            )
        return _client


def _payload(result) -> dict:
    return {
        "method": result.method,
        "endpoint": result.endpoint,
        "data": result.data,
    }


def _query(path: str) -> dict[str, str]:
    parsed = urlparse(path)
    values = parse_qs(parsed.query)
    return {key: items[0] for key, items in values.items() if items}


def _symbol(query: dict[str, str]) -> str:
    return (query.get("symbol") or "PTT").strip().upper()


def _interval(query: dict[str, str]) -> str:
    return (query.get("interval") or "1d").strip()


def _limit(query: dict[str, str]) -> int:
    raw = query.get("limit", "10")
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ConfigError("limit must be an integer.") from exc
    if limit < 1:
        raise ConfigError("limit must be at least 1.")
    return limit


def _endpoint_catalog() -> dict:
    sandbox = Settings.from_env().broker_id.upper() == "SANDBOX"
    base = hosts(sandbox)
    return {
        "environment": "uat" if sandbox else "prod",
        "open_api": base["open_api"],
        "market": base["market"],
        "endpoints": [
            {
                "group": item.group,
                "method": item.method,
                "path": item.path.format(
                    open_api=base["open_api"],
                    market=base["market"],
                    broker="{broker}",
                    app_code="{app_code}",
                    account="{account}",
                    symbol="{symbol}",
                    order_no="{order_no}",
                ),
                "sdk": item.sdk,
                "description": item.description,
            }
            for item in ENDPOINTS
        ],
    }


ROUTES = (
    "GET /",
    "GET /api/quote?symbol=PTT",
    "GET /api/set",
    "GET /api/value?symbol=PTT",
    "GET /api/candles?symbol=PTT&interval=1d&limit=10",
    "GET /api/account",
    "GET /api/orders",
    "GET /api/portfolios",
    "GET /api/trades",
    "GET /api/derivatives/account",
    "GET /api/derivatives/orders",
    "GET /api/derivatives/portfolios",
    "GET /api/endpoints",
)

DATA_ROUTES = {
    "/",
    "/api",
    "/api/quote",
    "/api/set",
    "/api/value",
    "/api/candles",
    "/api/account",
    "/api/orders",
    "/api/portfolios",
    "/api/trades",
    "/api/derivatives/account",
    "/api/derivatives/orders",
    "/api/derivatives/portfolios",
}


def build_response(path: str) -> tuple[int, dict]:
    parsed = urlparse(path)
    route = parsed.path.rstrip("/") or "/"
    query = _query(path)

    if route == "/api/endpoints":
        return 200, _endpoint_catalog()
    if route not in DATA_ROUTES:
        return 404, {"error": "Unknown route.", "routes": list(ROUTES)}

    if route in ("/", "/api", "/api/set"):
        body = set_numbers()
        _log(
            "[settrade] GET https://www.set.or.th/api/set/index/SET/info "
            f"set={body.get('set')} value={body.get('value')}"
        )
        return 200, body

    client = get_client()
    symbol = _symbol(query)
    interval = _interval(query)
    limit = _limit(query)

    if route in ("/", "/api", "/api/quote"):
        _log(f"[settrade] fetching all JSON symbol={symbol} interval={interval}")
        return 200, client.fetch_all(
            symbol,
            interval=interval,
            limit=limit,
            on_result=_log_settrade,
        )

    actions = {
        "/api/set": lambda: client.set_index(query.get("market") or "SET"),
        "/api/value": lambda: client.price_info(symbol),
        "/api/candles": lambda: client.candlesticks(
            symbol, interval=interval, limit=limit
        ),
        "/api/account": client.equity_account,
        "/api/orders": client.equity_orders,
        "/api/portfolios": client.equity_portfolios,
        "/api/trades": client.equity_trades,
        "/api/derivatives/account": client.derivatives_account,
        "/api/derivatives/orders": client.derivatives_orders,
        "/api/derivatives/portfolios": client.derivatives_portfolios,
    }
    body = _payload(actions[route]())
    _log_settrade(route, body)
    return 200, body


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        try:
            status, body = build_response(self.path)
        except ConfigError as exc:
            _log(f"[settrade] FAILED {exc}")
            status, body = 400, {"error": str(exc)}
        except SettradeError as exc:
            _log(f"[settrade] FAILED {exc}")
            status = exc.status_code if 400 <= exc.status_code < 600 else 502
            status, body = status, {"error": str(exc), "code": exc.code}
        except Exception as exc:
            _log(f"[settrade] FAILED {type(exc).__name__}: {exc}")
            traceback.print_exc()
            status, body = 500, {"error": f"{type(exc).__name__}: {exc}"}
        payload = json.dumps(body, indent=2, default=str, ensure_ascii=False).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[request] {self.address_string()} {fmt % args}", flush=True)


def serve(host: str = "127.0.0.1", port: int = 1717) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Settrade server  http://{host}:{port}", flush=True)
    print(f"SET last and value  http://{host}:{port}/", flush=True)
    print(f"Loaded env from  {ROOT / '.env'}", flush=True)
    print("Listening. The server stays quiet until a request arrives.", flush=True)
    print(
        f"Test from this machine: curl -sS http://127.0.0.1:{port}/api/endpoints",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
