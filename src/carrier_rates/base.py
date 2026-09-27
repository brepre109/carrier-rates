"""Shared plumbing for carrier clients: JSON I/O, token lifecycle, error mapping."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any

from .auth import TokenCache
from .config import BASE_URLS, CarrierCredentials, resolve_env
from .errors import AuthError, CarrierAPIError, ErrorDetail, ValidationError
from .models import RateQuote, Shipment
from .transport import HttpRequest, HttpResponse, Transport, UrllibTransport


def decode_json(response: HttpResponse) -> Any:
    """Parse JSON with Decimal floats so money never passes through binary floating point."""
    if not response.body:
        return None
    try:
        return json.loads(response.body, parse_float=Decimal)
    except ValueError:
        return None


class CarrierClient(ABC):
    carrier: str = ""

    def __init__(self, credentials: CarrierCredentials, *, env: str | None = None,
                 transport: Transport | None = None, token_cache: TokenCache | None = None,
                 base_url: str | None = None):
        self.credentials = credentials
        self.env = resolve_env(env)
        self.transport = transport or UrllibTransport()
        self.token_cache = token_cache or TokenCache()
        self.base_url = (base_url or BASE_URLS[self.carrier][self.env]).rstrip("/")

    # --- public API -----------------------------------------------------------------------

    def rate(self, shipment: Shipment, *, service: str | None = None,
             include_list_rates: bool = False) -> list[RateQuote]:
        """Quote a shipment. By default one quote per service: the account rate when the
        carrier returns one, otherwise the list rate. ``include_list_rates`` keeps both."""
        payload = self.build_rate_request(shipment, service=service)
        body = self._authorized_post(self.rate_url(service), payload)
        quotes = self.parse_rate_response(body)
        return quotes if include_list_rates else prefer_account_rates(quotes)

    # --- carrier-specific hooks ----------------------------------------------------------

    @abstractmethod
    def build_rate_request(self, shipment: Shipment, *, service: str | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def parse_rate_response(self, body: Any) -> list[RateQuote]: ...

    @abstractmethod
    def rate_url(self, service: str | None) -> str: ...

    @abstractmethod
    def _fetch_token(self) -> tuple[str, float]:
        """Return (access_token, expires_in_seconds)."""

    @abstractmethod
    def _error_details(self, body: Any) -> list[ErrorDetail]: ...

    def _rate_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                "Accept": "application/json"}

    # --- plumbing ------------------------------------------------------------------------

    @property
    def _cache_key(self) -> tuple[str, str, str]:
        return (self.carrier, self.base_url, self.credentials.client_id)

    def access_token(self) -> str:
        token = self.token_cache.get(self._cache_key)
        if token is None:
            token, expires_in = self._fetch_token()
            self.token_cache.put(self._cache_key, token, expires_in)
        return token

    def _authorized_post(self, url: str, payload: dict[str, Any]) -> Any:
        data = json.dumps(payload).encode()
        for attempt in (1, 2):
            request = HttpRequest("POST", url, self._rate_headers(self.access_token()), data)
            response = self.transport.send(request)
            try:
                return self._check(response)
            except AuthError:
                # A cached token can be revoked early; drop it and retry exactly once.
                self.token_cache.invalidate(self._cache_key)
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    def _check(self, response: HttpResponse) -> Any:
        body = decode_json(response)
        if 200 <= response.status < 300:
            return body
        details = self._error_details(body)
        if not details and response.body and body is None:
            details = [ErrorDetail("", response.body[:200].decode("utf-8", "replace"))]
        if response.status in (401, 403):
            raise AuthError(self.carrier, response.status, details)
        if response.status in (400, 404, 422):
            raise ValidationError(self.carrier, response.status, details)
        raise CarrierAPIError(self.carrier, response.status, details)


def prefer_account_rates(quotes: list[RateQuote]) -> list[RateQuote]:
    """Keep one quote per (carrier, service): account rate if present, else list rate."""
    best: dict[tuple[str, str], RateQuote] = {}
    for q in quotes:
        key = (q.carrier, q.service_code)
        current = best.get(key)
        if current is None or (current.is_list_rate and not q.is_list_rate):
            best[key] = q
    return list(best.values())
