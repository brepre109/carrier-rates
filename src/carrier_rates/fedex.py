"""FedEx REST APIs: OAuth (POST /oauth/token) and Rate (POST /rate/v1/rates/quotes)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from .base import CarrierClient, decode_json
from .errors import AuthError, ErrorDetail
from .models import Address, RateQuote, Shipment, Surcharge
from .transport import HttpRequest
from .units import ceil_to

_NUM_WORDS = {w: i for i, w in enumerate(
    "ZERO ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN ELEVEN TWELVE THIRTEEN FOURTEEN "
    "FIFTEEN SIXTEEN SEVENTEEN EIGHTEEN NINETEEN TWENTY".split())}


class FedExClient(CarrierClient):
    carrier = "fedex"

    pickup_type = "DROPOFF_AT_FEDEX_LOCATION"
    packaging_type = "YOUR_PACKAGING"

    def rate_url(self, service: str | None) -> str:
        return f"{self.base_url}/rate/v1/rates/quotes"

    def _rate_headers(self, token: str) -> dict[str, str]:
        headers = super()._rate_headers(token)
        headers["X-locale"] = "en_US"
        return headers

    def _fetch_token(self) -> tuple[str, float]:
        # FedEx wants the client credentials in the form body, not HTTP Basic.
        form = urlencode({
            "grant_type": "client_credentials",
            "client_id": self.credentials.client_id,
            "client_secret": self.credentials.client_secret,
        }).encode()
        request = HttpRequest("POST", f"{self.base_url}/oauth/token",
                              {"Content-Type": "application/x-www-form-urlencoded"}, form)
        response = self.transport.send(request)
        if response.status >= 400:
            # A bad client id/secret comes back as 401 (sometimes 400); either way it is auth.
            raise AuthError(self.carrier, response.status, self._error_details(decode_json(response)))
        body = self._check(response)
        try:
            return body["access_token"], float(body.get("expires_in", 3600))
        except (TypeError, KeyError) as exc:
            raise AuthError(self.carrier, response.status, message="token response had no access_token") from exc

    def _error_details(self, body: Any) -> list[ErrorDetail]:
        errors = (body or {}).get("errors") if isinstance(body, dict) else None
        return [ErrorDetail(str(e.get("code", "")), str(e.get("message", ""))) for e in errors or []]

    # --- request ---------------------------------------------------------------------------

    @staticmethod
    def _address(addr: Address, *, recipient: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"postalCode": addr.postal_code, "countryCode": addr.country_code}
        if addr.lines:
            out["streetLines"] = list(addr.lines)
        if addr.city:
            out["city"] = addr.city
        if addr.state:
            out["stateOrProvinceCode"] = addr.state
        if recipient:
            out["residential"] = addr.residential
        return out

    def build_rate_request(self, shipment: Shipment, *, service: str | None = None) -> dict[str, Any]:
        packages = []
        for p in shipment.packages:
            packages.append({
                "weight": {"units": "LB", "value": float(ceil_to(p.weight_lb, "0.1"))},
                # FedEx rejects fractional dimensions: round each side up to a whole inch.
                "dimensions": {
                    "length": int(ceil_to(p.length_in, "1")),
                    "width": int(ceil_to(p.width_in, "1")),
                    "height": int(ceil_to(p.height_in, "1")),
                    "units": "IN",
                },
            })
        requested: dict[str, Any] = {
            "shipper": {"address": self._address(shipment.origin)},
            "recipient": {"address": self._address(shipment.destination, recipient=True)},
            "pickupType": self.pickup_type,
            "packagingType": self.packaging_type,
            "rateRequestType": ["ACCOUNT", "LIST"],
            "totalPackageCount": len(packages),
            "requestedPackageLineItems": packages,
        }
        if shipment.ship_date:
            requested["shipDateStamp"] = shipment.ship_date.isoformat()
        if service:
            requested["serviceType"] = service
        payload: dict[str, Any] = {
            "rateRequestControlParameters": {"returnTransitTimes": True},
            "requestedShipment": requested,
        }
        if self.credentials.account_number:
            payload["accountNumber"] = {"value": self.credentials.account_number}
        return payload

    # --- response --------------------------------------------------------------------------

    def parse_rate_response(self, body: Any) -> list[RateQuote]:
        details = ((body or {}).get("output") or {}).get("rateReplyDetails") or []
        quotes: list[RateQuote] = []
        for reply in details:
            code = str(reply.get("serviceType", ""))
            name = str(reply.get("serviceName") or code.replace("_", " ").title())
            transit_days, delivery = _transit(reply)
            for rated in reply.get("ratedShipmentDetails") or []:
                total = rated.get("totalNetCharge")
                if total is None:
                    continue
                detail = rated.get("shipmentRateDetail") or {}
                weight = detail.get("totalBillingWeight") or {}
                quotes.append(RateQuote(
                    carrier=self.carrier,
                    service_code=code,
                    service_name=name,
                    total_charge=Decimal(str(total)),
                    currency=str(rated.get("currency") or detail.get("currency") or "USD"),
                    is_list_rate=str(rated.get("rateType", "")).upper() == "LIST",
                    billable_weight=_dec(weight.get("value")),
                    billable_weight_unit=weight.get("units"),
                    transit_days=transit_days,
                    delivery_date=delivery,
                    surcharges=tuple(
                        Surcharge(str(s.get("type", "")), str(s.get("description", "")), Decimal(str(s["amount"])))
                        for s in detail.get("surCharges") or [] if s.get("amount") is not None
                    ),
                ))
        return quotes


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _words_to_days(value: Any) -> int | None:
    """``"THREE_DAYS"`` -> 3, ``"ONE_DAY"`` -> 1."""
    if not value:
        return None
    m = re.match(r"^([A-Z]+)_DAYS?$", str(value).upper())
    return _NUM_WORDS.get(m.group(1)) if m else None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:19]).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def _transit(reply: dict[str, Any]) -> tuple[int | None, date | None]:
    commit = reply.get("commit") or {}
    op = reply.get("operationalDetail") or {}
    days = (_words_to_days((commit.get("transitDays") or {}).get("minimumTransitTime"))
            or _words_to_days(op.get("transitTime")))
    delivery = _parse_date((commit.get("dateDetail") or {}).get("dayFormat")) or _parse_date(op.get("deliveryDate"))
    return days, delivery
