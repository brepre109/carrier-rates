"""UPS OAuth (POST /security/v1/oauth/token) and Rating (POST /api/rating/{version}/{option})."""

from __future__ import annotations

import base64
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .base import CarrierClient, decode_json
from .config import CarrierCredentials
from .errors import AuthError, ErrorDetail
from .models import Address, RateQuote, Shipment, Surcharge
from .transport import HttpRequest
from .units import ceil_to

SERVICE_NAMES = {
    "01": "UPS Next Day Air",
    "02": "UPS 2nd Day Air",
    "03": "UPS Ground",
    "07": "UPS Worldwide Express",
    "08": "UPS Worldwide Expedited",
    "11": "UPS Standard",
    "12": "UPS 3 Day Select",
    "13": "UPS Next Day Air Saver",
    "14": "UPS Next Day Air Early",
    "54": "UPS Worldwide Express Plus",
    "59": "UPS 2nd Day Air A.M.",
    "65": "UPS Worldwide Saver",
}

# Only codes we are confident about; anything else is labelled generically.
CHARGE_NAMES = {"375": "Fuel surcharge", "270": "Residential surcharge"}


class UPSClient(CarrierClient):
    carrier = "ups"

    def __init__(self, credentials: CarrierCredentials, *, version: str = "v2409",
                 transit: bool = False, **kwargs: Any):
        super().__init__(credentials, **kwargs)
        self.version = version
        self.transit = transit

    def rate_url(self, service: str | None) -> str:
        if service:
            option = "Ratetimeintransit" if self.transit else "Rate"
        else:
            option = "Shoptimeintransit" if self.transit else "Shop"
        return f"{self.base_url}/api/rating/{self.version}/{option}"

    def _rate_headers(self, token: str) -> dict[str, str]:
        headers = super()._rate_headers(token)
        headers["transId"] = uuid.uuid4().hex  # max 32 chars
        headers["transactionSrc"] = "carrier-rates"
        return headers

    def _fetch_token(self) -> tuple[str, float]:
        # UPS uses HTTP Basic with client_id:client_secret and a form body.
        basic = base64.b64encode(
            f"{self.credentials.client_id}:{self.credentials.client_secret}".encode()).decode()
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {basic}"}
        if self.credentials.account_number:
            headers["x-merchant-id"] = self.credentials.account_number
        request = HttpRequest("POST", f"{self.base_url}/security/v1/oauth/token", headers,
                              b"grant_type=client_credentials")
        response = self.transport.send(request)
        if response.status >= 400:
            raise AuthError(self.carrier, response.status, self._error_details(decode_json(response)))
        body = self._check(response)
        try:
            # expires_in arrives as a *string* of seconds (e.g. "14399").
            return body["access_token"], float(body.get("expires_in", 3600))
        except (TypeError, KeyError, ValueError) as exc:
            raise AuthError(self.carrier, response.status, message="token response had no access_token") from exc

    def _error_details(self, body: Any) -> list[ErrorDetail]:
        if not isinstance(body, dict):
            return []
        errors = (body.get("response") or {}).get("errors") or body.get("errors") or []
        return [ErrorDetail(str(e.get("code", "")), str(e.get("message", ""))) for e in errors]

    # --- request ---------------------------------------------------------------------------

    @staticmethod
    def _address(addr: Address, *, ship_to: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"PostalCode": addr.postal_code, "CountryCode": addr.country_code}
        if addr.lines:
            out["AddressLine"] = list(addr.lines)
        if addr.city:
            out["City"] = addr.city
        if addr.state:
            out["StateProvinceCode"] = addr.state
        if ship_to and addr.residential:
            out["ResidentialAddressIndicator"] = ""  # presence is the flag; value is ignored
        return out

    def build_rate_request(self, shipment: Shipment, *, service: str | None = None) -> dict[str, Any]:
        account = self.credentials.account_number
        shipper: dict[str, Any] = {"Address": self._address(shipment.origin)}
        if account:
            shipper["ShipperNumber"] = account
        packages = [{
            "PackagingType": {"Code": "02", "Description": "Customer supplied package"},
            "Dimensions": {
                "UnitOfMeasurement": {"Code": "IN"},
                "Length": _num(ceil_to(p.length_in, "0.01")),
                "Width": _num(ceil_to(p.width_in, "0.01")),
                "Height": _num(ceil_to(p.height_in, "0.01")),
            },
            "PackageWeight": {"UnitOfMeasurement": {"Code": "LBS"}, "Weight": _num(ceil_to(p.weight_lb, "0.1"))},
        } for p in shipment.packages]
        body: dict[str, Any] = {
            "Shipper": shipper,
            "ShipTo": {"Address": self._address(shipment.destination, ship_to=True)},
            "ShipFrom": {"Address": self._address(shipment.origin)},
            "NumOfPieces": str(len(packages)),
            "Package": packages,
        }
        if account:
            body["PaymentDetails"] = {"ShipmentCharge": [{"Type": "01", "BillShipper": {"AccountNumber": account}}]}
            body["ShipmentRatingOptions"] = {"NegotiatedRatesIndicator": "Y"}
        if service:
            body["Service"] = {"Code": service}
        if self.transit:
            pickup = shipment.ship_date or date.today()
            body["DeliveryTimeInformation"] = {"PackageBillType": "03", "Pickup": {"Date": pickup.strftime("%Y%m%d")}}
            body["ShipmentTotalWeight"] = {"UnitOfMeasurement": {"Code": "LBS"},
                                           "Weight": _num(ceil_to(shipment.total_weight_lb, "0.1"))}
        return {"RateRequest": {"Request": {"TransactionReference": {"CustomerContext": "carrier-rates"}},
                                "Shipment": body}}

    # --- response --------------------------------------------------------------------------

    def parse_rate_response(self, body: Any) -> list[RateQuote]:
        rated = ((body or {}).get("RateResponse") or {}).get("RatedShipment") or []
        if isinstance(rated, dict):  # a single-service "Rate" call returns an object, not a list
            rated = [rated]
        quotes: list[RateQuote] = []
        for r in rated:
            code = str((r.get("Service") or {}).get("Code", ""))
            name = SERVICE_NAMES.get(code) or (r.get("Service") or {}).get("Description") or f"UPS service {code}"
            weight = r.get("BillingWeight") or {}
            transit_days, delivery = _transit(r)
            common = dict(
                carrier=self.carrier,
                service_code=code,
                service_name=name,
                billable_weight=_dec(weight.get("Weight")),
                billable_weight_unit=(weight.get("UnitOfMeasurement") or {}).get("Code"),
                transit_days=transit_days,
                delivery_date=delivery,
            )
            total = r.get("TotalCharges") or {}
            if total.get("MonetaryValue") is not None:
                quotes.append(RateQuote(
                    total_charge=Decimal(str(total["MonetaryValue"])),
                    currency=str(total.get("CurrencyCode") or "USD"),
                    is_list_rate=True,
                    surcharges=_charges(r),
                    **common,
                ))
            negotiated = r.get("NegotiatedRateCharges") or {}
            neg_total = negotiated.get("TotalCharge") or {}
            if neg_total.get("MonetaryValue") is not None:
                quotes.append(RateQuote(
                    total_charge=Decimal(str(neg_total["MonetaryValue"])),
                    currency=str(neg_total.get("CurrencyCode") or "USD"),
                    is_list_rate=False,
                    surcharges=_charges(negotiated),
                    **common,
                ))
        return quotes


def _num(value: Decimal) -> str:
    """UPS wants numbers as strings; drop trailing zeros ("5.0" -> "5")."""
    text = format(value.normalize(), "f")
    return text


def _dec(value: Any) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _charges(node: dict[str, Any]) -> tuple[Surcharge, ...]:
    out = []
    for c in _as_list(node.get("ItemizedCharges")):
        amount = _dec(c.get("MonetaryValue"))
        if amount:
            code = str(c.get("Code", ""))
            desc = c.get("Description") or CHARGE_NAMES.get(code) or f"UPS charge {code}"
            out.append(Surcharge(code, str(desc), amount))
    options = _dec((node.get("ServiceOptionsCharges") or {}).get("MonetaryValue"))
    if options:
        out.append(Surcharge("SERVICE_OPTIONS", "Service options", options))
    return tuple(out)


def _transit(r: dict[str, Any]) -> tuple[int | None, date | None]:
    days: int | None = None
    delivery: date | None = None
    arrival = ((r.get("TimeInTransit") or {}).get("ServiceSummary") or {}).get("EstimatedArrival") or {}
    if arrival.get("BusinessDaysInTransit"):
        days = int(arrival["BusinessDaysInTransit"])
    raw_date = (arrival.get("Arrival") or {}).get("Date")
    if raw_date:
        try:
            delivery = datetime.strptime(str(raw_date), "%Y%m%d").date()
        except ValueError:
            delivery = None
    guaranteed = (r.get("GuaranteedDelivery") or {}).get("BusinessDaysInTransit")
    if days is None and guaranteed:
        days = int(guaranteed)
    return days, delivery
