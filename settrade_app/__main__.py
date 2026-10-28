import argparse
import json
import sys

from settrade_v2.errors import SettradeError

from .client import ApiResult, SettradeClient
from .endpoints import ENDPOINTS, hosts
from .settings import ConfigError, Settings


def _print_result(result: ApiResult) -> None:
    print(f"{result.method} {result.endpoint}")
    print(json.dumps(result.data, indent=2, default=str, ensure_ascii=False))


def _run(action) -> None:
    try:
        _print_result(action())
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc
    except SettradeError as exc:
        raise SystemExit(f"Settrade API error: {exc}") from exc


def _print_endpoints(sandbox: bool) -> None:
    base = hosts(sandbox)
    label = "UAT / sandbox" if sandbox else "production"
    print(f"Environment: {label}")
    print(f"Open API: {base['open_api']}")
    print(f"Market:   {base['market']}")
    print()
    current = ""
    for item in ENDPOINTS:
        if item.group != current:
            current = item.group
            print(f"[{current}]")
        path = item.path.format(
            open_api=base["open_api"],
            market=base["market"],
            broker="{broker}",
            app_code="{app_code}",
            account="{account}",
            symbol="{symbol}",
            order_no="{order_no}",
        )
        print(f"  {item.method:<6} {path}")
        print(f"         {item.sdk} — {item.description}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m settrade_app",
        description="Fetch Settrade Open API market and account data.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Show production hosts in the endpoints list. Calls still follow .env.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("endpoints", help="Print the REST endpoints this SDK calls.")

    serve = sub.add_parser("serve", help="Start the local GET server.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)

    quote = sub.add_parser("quote", help="Get the latest quote for a symbol.")
    quote.add_argument("symbol", help="Symbol, for example PTT or S50Z25.")

    candles = sub.add_parser("candles", help="Get candlestick bars.")
    candles.add_argument("symbol")
    candles.add_argument(
        "--interval",
        default="1d",
        help="1m, 5m, 15m, 30m, 60m, 1d, 1w, or 1M. Default: 1d.",
    )
    candles.add_argument("--limit", type=int, default=10)
    candles.add_argument("--start", help="Start date, if the API accepts it.")
    candles.add_argument("--end", help="End date, if the API accepts it.")

    sub.add_parser("account", help="Equity account info.")
    sub.add_parser("orders", help="Equity orders.")
    sub.add_parser("portfolios", help="Equity holdings.")
    sub.add_parser("trades", help="Equity trades.")
    sub.add_parser("deriv-account", help="Derivatives account info.")
    sub.add_parser("deriv-orders", help="Derivatives orders.")
    sub.add_parser("deriv-portfolios", help="Derivatives positions.")

    args = parser.parse_args(argv)
    if args.command == "endpoints":
        _print_endpoints(sandbox=not args.live)
        return

    if args.command == "serve":
        from .server import serve

        serve(args.host, args.port)
        return

    try:
        client = SettradeClient(Settings.from_env())
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Open API: {client.open_api_url}")
    print(f"Market:   {client.market_url}")
    print(f"Broker:   {client.broker_id}")
    print()

    actions = {
        "quote": lambda: client.quote(args.symbol),
        "candles": lambda: client.candlesticks(
            args.symbol,
            interval=args.interval,
            limit=args.limit,
            start=args.start,
            end=args.end,
        ),
        "account": client.equity_account,
        "orders": client.equity_orders,
        "portfolios": client.equity_portfolios,
        "trades": client.equity_trades,
        "deriv-account": client.derivatives_account,
        "deriv-orders": client.derivatives_orders,
        "deriv-portfolios": client.derivatives_portfolios,
    }
    _run(actions[args.command])


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
