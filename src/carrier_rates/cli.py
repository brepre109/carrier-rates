"""``carrier-rates`` command line."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from typing import Sequence

from . import __version__
from .auth import TokenCache
from .config import load_dotenv
from .errors import CarrierRatesError
from .models import Address, Package, RateQuote, Shipment
from .service import CARRIERS, QuoteResult, build_client, cheapest, quote_all
from .units import parse_dims


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="carrier-rates", description="Normalized FedEx / UPS rate quotes.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("quote", help="quote one shipment across carriers")
    q.add_argument("--from", dest="origin", required=True, help="origin postal code")
    q.add_argument("--to", dest="destination", required=True, help="destination postal code")
    q.add_argument("--from-country", default="US")
    q.add_argument("--to-country", default="US")
    q.add_argument("--residential", action="store_true", help="destination is a residence")
    q.add_argument("--weight", required=True, type=str, help="package weight (default lb)")
    q.add_argument("--weight-unit", default="lb", choices=["lb", "kg", "oz", "g"])
    q.add_argument("--dims", required=True, help="LxWxH, e.g. 12x10x8 (default inches)")
    q.add_argument("--dim-unit", default="in", choices=["in", "cm", "mm"])
    q.add_argument("--packages", type=int, default=1, help="number of identical packages")
    q.add_argument("--ship-date", type=date.fromisoformat, help="YYYY-MM-DD")
    q.add_argument("--carrier", default="all", choices=["all", *CARRIERS])
    q.add_argument("--fixtures", action="store_true",
                   help="replay bundled fixtures instead of calling the APIs (default when no credentials)")
    q.add_argument("--fixture-dir", help="replay fixtures from this directory instead of the bundled ones")
    q.add_argument("--record", metavar="DIR", help="live mode: save sanitized responses to DIR as fixtures")
    q.add_argument("--list-rates", action="store_true", help="show list rates alongside account rates")
    q.add_argument("--max-days", type=int, help="highlight the cheapest quote within N transit days")
    q.add_argument("--json", action="store_true", help="print JSON instead of a table")
    q.add_argument("--env-file", default=".env", help="dotenv file to load if present (default ./.env)")
    return p


def _table(quotes: list[RateQuote], best: RateQuote | None) -> str:
    headers = ["", "CARRIER", "SERVICE", "TOTAL", "RATE", "BILL WT", "DAYS", "DELIVERY", "SURCHARGES"]
    rows = []
    for q in quotes:
        rows.append([
            "*" if q is best else "",
            q.carrier.upper(),
            q.service_name,
            f"{q.total_charge:.2f} {q.currency}",
            q.rate_type,
            f"{q.billable_weight} {q.billable_weight_unit or ''}".strip() if q.billable_weight is not None else "-",
            str(q.transit_days) if q.transit_days is not None else "-",
            q.delivery_date.isoformat() if q.delivery_date else "-",
            f"{sum(s.amount for s in q.surcharges):.2f}" if q.surcharges else "-",
        ])
    widths = [max(len(str(r[i])) for r in [headers, *rows]) for i in range(len(headers))]
    right = {3, 5, 6, 8}
    fmt = lambda r: "  ".join(str(c).rjust(w) if i in right else str(c).ljust(w)  # noqa: E731
                              for i, (c, w) in enumerate(zip(r, widths))).rstrip()
    return "\n".join([fmt(headers), fmt(["-" * w for w in widths]), *map(fmt, rows)])


def _quote(args: argparse.Namespace) -> int:
    if args.env_file:
        load_dotenv(args.env_file)
    try:
        dims = parse_dims(args.dims, args.dim_unit)
        pkg = Package.create(args.weight, dims, weight_unit=args.weight_unit)
        shipment = Shipment(
            origin=Address(args.origin, args.from_country),
            destination=Address(args.destination, args.to_country, residential=args.residential),
            packages=(pkg,) * max(args.packages, 1),
            ship_date=args.ship_date,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    carriers = list(CARRIERS) if args.carrier == "all" else [args.carrier]
    use_fixtures = True if (args.fixtures or args.fixture_dir) else None
    cache = TokenCache()
    clients, sources = {}, {}
    for name in carriers:
        try:
            clients[name], sources[name] = build_client(
                name, use_fixtures=use_fixtures, fixture_dir=args.fixture_dir,
                record_dir=args.record, token_cache=cache)
        except CarrierRatesError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    result: QuoteResult = quote_all(shipment, clients, include_list_rates=args.list_rates)
    result.sources = sources
    best = cheapest(result.quotes, max_transit_days=args.max_days)

    if args.json:
        print(json.dumps({
            "sources": sources,
            "cheapest": best.to_dict() if best else None,
            "quotes": [q.to_dict() for q in result.quotes],
            "errors": {k: str(v) for k, v in result.errors.items()},
        }, indent=2))
    else:
        fixture_carriers = [c for c, s in sources.items() if s == "fixtures"]
        if fixture_carriers:
            print(f"note: {', '.join(fixture_carriers)} replayed from fixtures (sample data, not live prices)",
                  file=sys.stderr)
        if result.quotes:
            print(_table(result.quotes, best))
            if best:
                window = f" within {args.max_days} days" if args.max_days is not None else ""
                print(f"\ncheapest{window}: {best.service_name} ({best.carrier}, {best.rate_type} rate) "
                      f"{best.total_charge:.2f} {best.currency}")
            elif args.max_days is not None:
                print(f"\nno quote with a known transit time of {args.max_days} days or less")
        for name, err in result.errors.items():
            print(f"{name}: {type(err).__name__}: {err}", file=sys.stderr)
    return 0 if result.quotes else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "quote":
        return _quote(args)
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
