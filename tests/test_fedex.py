import json
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs

import pytest

from carrier_rates.errors import AuthError, ValidationError
from conftest import MixedFixtures


def test_request_body(fedex, shipment):
    body = fedex.build_rate_request(shipment)
    rs = body["requestedShipment"]
    assert body["accountNumber"] == {"value": "TESTACCT1"}
    assert body["rateRequestControlParameters"] == {"returnTransitTimes": True}
    assert rs["rateRequestType"] == ["ACCOUNT", "LIST"]
    assert rs["shipper"]["address"] == {"postalCode": "90210", "countryCode": "US", "stateOrProvinceCode": "CA"}
    assert rs["recipient"]["address"]["residential"] is True
    assert rs["shipDateStamp"] == "2026-09-28"
    pkg = rs["requestedPackageLineItems"][0]
    assert pkg["weight"] == {"units": "LB", "value": 5.0}
    assert pkg["dimensions"] == {"length": 12, "width": 10, "height": 8, "units": "IN"}
    assert "serviceType" not in rs
    assert fedex.build_rate_request(shipment, service="FEDEX_GROUND")["requestedShipment"]["serviceType"] == "FEDEX_GROUND"


def test_fractional_dimensions_round_up(fedex, shipment):
    from carrier_rates import Package, Shipment
    s = Shipment(shipment.origin, shipment.destination, (Package.create("4.01", ("12.2", "10", "7.5")),))
    pkg = fedex.build_rate_request(s)["requestedShipment"]["requestedPackageLineItems"][0]
    assert pkg["dimensions"]["length"] == 13 and pkg["dimensions"]["height"] == 8
    assert pkg["weight"]["value"] == 4.1


def test_http_calls(fedex, shipment):
    fedex.rate(shipment)
    token_req, rate_req = fedex.transport.requests
    assert token_req.url == "https://apis-sandbox.fedex.com/oauth/token"
    form = parse_qs(token_req.body.decode())
    assert form == {"grant_type": ["client_credentials"], "client_id": ["test-client-id"],
                    "client_secret": ["test-client-secret"]}
    assert rate_req.url == "https://apis-sandbox.fedex.com/rate/v1/rates/quotes"
    assert rate_req.headers["Authorization"] == "Bearer fixture-fedex-access-token"
    assert json.loads(rate_req.body)["requestedShipment"]["totalPackageCount"] == 1


def test_production_only_when_explicit(shipment):
    from carrier_rates.config import CarrierCredentials
    from carrier_rates.fedex import FedExClient
    creds = CarrierCredentials("a", "b")
    assert FedExClient(creds).base_url == "https://apis-sandbox.fedex.com"
    assert FedExClient(creds, env="production").base_url == "https://apis.fedex.com"


def test_parse_prefers_account_rates(fedex, shipment):
    quotes = {q.service_code: q for q in fedex.rate(shipment)}
    assert set(quotes) == {"FEDEX_GROUND", "FEDEX_2_DAY", "STANDARD_OVERNIGHT"}
    ground = quotes["FEDEX_GROUND"]
    assert ground.total_charge == Decimal("13.95") and not ground.is_list_rate
    assert isinstance(ground.total_charge, Decimal)
    assert ground.billable_weight == Decimal("7.0") and ground.billable_weight_unit == "LB"
    assert ground.transit_days == 5 and ground.delivery_date is None
    assert [s.code for s in ground.surcharges] == ["FUEL", "RESIDENTIAL_DELIVERY"]
    assert quotes["FEDEX_2_DAY"].delivery_date == date(2026, 9, 30)
    overnight = quotes["STANDARD_OVERNIGHT"]  # only a LIST rate came back
    assert overnight.is_list_rate and overnight.transit_days == 1


def test_include_list_rates(fedex, shipment):
    quotes = fedex.rate(shipment, include_list_rates=True)
    assert len(quotes) == 5
    ground_list = [q for q in quotes if q.service_code == "FEDEX_GROUND" and q.is_list_rate][0]
    assert ground_list.total_charge == Decimal("17.62")


def test_money_is_exact(fedex, shipment):
    ground = [q for q in fedex.rate(shipment) if q.service_code == "FEDEX_GROUND"][0]
    assert sum(s.amount for s in ground.surcharges) == Decimal("2.55")


def test_validation_error(fedex, shipment):
    fedex.transport = MixedFixtures({"fedex_rate": ["fedex_rate_validation"]})
    with pytest.raises(ValidationError) as info:
        fedex.rate(shipment)
    assert info.value.status == 400
    assert info.value.details[0].code == "RECIPIENT.POSTALCODE.INVALID"


def test_auth_error(fedex, shipment):
    fedex.transport = MixedFixtures({"fedex_oauth": ["fedex_oauth_denied"]})
    with pytest.raises(AuthError) as info:
        fedex.rate(shipment)
    assert "NOT.AUTHORIZED.ERROR" in str(info.value)
    assert "test-client-secret" not in str(info.value)
