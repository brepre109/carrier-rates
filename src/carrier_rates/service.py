"""Multi-carrier orchestration: build clients, quote everywhere, pick the cheapest."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from .auth import TokenCache
from .base import CarrierClient
from .config import CarrierCredentials, credentials_from_env, resolve_env
from .errors import CarrierRatesError
from .fedex import FedExClient
from .models import RateQuote, Shipment
from .transport import FixtureTransport, RecordingTransport, Transport, UrllibTransport
from .ups import UPSClient

CARRIERS: dict[str, type[CarrierClient]] = {"fedex": FedExClient, "ups": UPSClient}

# Obviously fake credentials used when replaying fixtures without a real account.
FIXTURE_CREDENTIALS = CarrierCredentials("fixture-client-id", "fixture-client-secret", "000000")


@dataclass
class QuoteResult:
    quotes: list[RateQuote] = field(default_factory=list)
    errors: dict[str, CarrierRatesError] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # carrier -> "live" | "fixtures"

    @property
    def cheapest(self) -> RateQuote | None:
        return cheapest(self.quotes)


def sort_quotes(quotes: Iterable[RateQuote]) -> list[RateQuote]:
    """Cheapest first; ties broken by faster transit, then carrier/service for stability."""
    return sorted(quotes, key=lambda q: (q.total_charge, q.transit_days if q.transit_days is not None else 99,
                                         q.carrier, q.service_code))


def cheapest(quotes: Iterable[RateQuote], *, max_transit_days: int | None = None,
             currency: str | None = None) -> RateQuote | None:
    """Cheapest quote, optionally limited to a delivery window and one currency.

    Quotes with unknown transit time are excluded when ``max_transit_days`` is set,
    because "unknown" cannot be shown to meet a deadline.
    """
    pool = [q for q in quotes
            if (currency is None or q.currency == currency)
            and (max_transit_days is None or (q.transit_days is not None and q.transit_days <= max_transit_days))]
    ordered = sort_quotes(pool)
    return ordered[0] if ordered else None


def build_client(carrier: str, *, environ: Mapping[str, str] | None = None, use_fixtures: bool | None = None,
                 fixture_dir: str | Path | None = None, record_dir: str | Path | None = None,
                 token_cache: TokenCache | None = None, transport: Transport | None = None,
                 ) -> tuple[CarrierClient, str]:
    """Build a client from environment variables.

    Returns (client, source). With ``use_fixtures=None`` fixtures are used automatically
    when credentials for that carrier are missing.
    """
    carrier = carrier.lower()
    if carrier not in CARRIERS:
        raise CarrierRatesError(f"unknown carrier {carrier!r}; choose from {sorted(CARRIERS)}")
    env_map = environ if environ is not None else os.environ
    creds = credentials_from_env(carrier, env_map)
    env = resolve_env(env_map.get("CARRIER_RATES_ENV"))
    fixtures = use_fixtures if use_fixtures is not None else creds is None
    if fixtures:
        if record_dir:
            raise CarrierRatesError("record mode needs live credentials; it cannot record fixtures from fixtures")
        client = CARRIERS[carrier](FIXTURE_CREDENTIALS, env="sandbox",
                                   transport=transport or FixtureTransport(fixture_dir), token_cache=token_cache)
        return client, "fixtures"
    if creds is None:
        raise CarrierRatesError(f"{carrier}: set {carrier.upper()}_CLIENT_ID and {carrier.upper()}_CLIENT_SECRET")
    live: Transport = transport or UrllibTransport()
    if record_dir:
        live = RecordingTransport(live, Path(record_dir),
                                  secrets=[creds.account_number or "", creds.client_id, creds.client_secret])
    return CARRIERS[carrier](creds, env=env, transport=live, token_cache=token_cache), "live"


def quote_all(shipment: Shipment, clients: Mapping[str, CarrierClient], *, include_list_rates: bool = False,
              ) -> QuoteResult:
    """Ask every client for rates. One carrier failing does not hide the others' quotes."""
    result = QuoteResult()
    for name, client in clients.items():
        try:
            result.quotes.extend(client.rate(shipment, include_list_rates=include_list_rates))
        except CarrierRatesError as exc:
            result.errors[name] = exc
    result.quotes = sort_quotes(result.quotes)
    return result
