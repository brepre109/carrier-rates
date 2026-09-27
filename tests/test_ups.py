import base64
import json
from datetime import date
from decimal import Decimal

import pytest

from carrier_rates.errors import AuthError, CarrierAPIError
from carrier_rates.ups import UPSClient
from conftest import CREDS, MixedFixtures


def test_request_body(ups, shipment):
    body = ups.build_rate_request(shipment)["RateRequest"]["Shipment"]
    assert body["Shipper"]["ShipperNumber"] == "TESTACCT1"
    assert body["ShipmentRatingOptions"] == {"NegotiatedRatesIndicator": "Y"}
    assert body["PaymentDetails"]["ShipmentCharge"][0]["BillShipper"]["AccountNumber"] == "TESTACCT1"
    assert body["ShipTo"]["Address"]["ResidentialAddressIndicator"] == ""
    assert "ResidentialAddressIndicator" not in body["ShipFrom"]["Address"]
    pkg = body["Package"][0]
    assert pkg["PackageWeight"] == {"UnitOfMeasurement": {"Code": "LBS"}, "Weight": "5"}
    assert (pkg["Dimensions"]["Length"], pkg["Dimensions"]["Width"], pkg["Dimensions"]["Height"]) == ("12", "10", "8")
    assert body["NumOfPieces"] == "1"
    assert "Service" not in body and "DeliveryTimeInformation" not in body


def test_no_account_means_no_negotiated_block(shipment):
    from carrier_rates.config import CarrierCredentials
    client = UPSClient(CarrierCredentials("id", "secret"))
    body = client.build_rate_request(shipment)["RateRequest"]["Shipment"]
    assert "ShipperNumber" not in body["Shipper"]
    assert "ShipmentRatingOptions" not in body and "PaymentDetails" not in body


def test_urls_by_option(ups):
    assert ups.rate_url(None) == "https://wwwcie.ups.com/api/rating/v2409/Shop"
    assert ups.rate_url("03") == "https://wwwcie.ups.com/api/rating/v2409/Rate"
    t = UPSClient(CREDS, transit=True, version="v2403", env="production")
    assert t.rate_url(None) == "https://onlinetools.ups.com/api/rating/v2403/Shoptimeintransit"


def test_http_calls(ups, shipment):
    ups.rate(shipment)
    token_req, rate_req = ups.transport.requests
    assert token_req.url == "https://wwwcie.ups.com/security/v1/oauth/token"
    assert token_req.body == b"grant_type=client_credentials"
    expected = base64.b64encode(b"test-client-id:test-client-secret").decode()
    assert token_req.headers["Authorization"] == f"Basic {expected}"
    assert token_req.headers["x-merchant-id"] == "TESTACCT1"
    assert rate_req.headers["Authorization"] == "Bearer fixture-ups-access-token"
    assert len(rate_req.headers["transId"]) <= 32
    assert json.loads(rate_req.body)["RateRequest"]["Shipment"]["Shipper"]["ShipperNumber"] == "TESTACCT1"


def test_parse_shop(ups, shipment):
    quotes = {q.service_code: q for q in ups.rate(shipment)}
    ground = quotes["03"]
    assert ground.service_name == "UPS Ground"
    assert ground.total_charge == Decimal("13.48") and not ground.is_list_rate
    assert ground.billable_weight == Decimal("7.0") and ground.billable_weight_unit == "LBS"
    assert [(s.code, s.amount) for s in ground.surcharges] == [("375", Decimal("2.02"))]
    assert quotes["12"].transit_days == 3
    nda = quotes["01"]  # no NegotiatedRateCharges -> list rate only
    assert nda.is_list_rate and nda.total_charge == Decimal("96.30") and nda.transit_days == 1


def test_list_rates_skip_zero_charges(ups, shipment):
    ground_list = [q for q in ups.rate(shipment, include_list_rates=True) if q.service_code == "03" and q.is_list_rate][0]
    assert ground_list.total_charge == Decimal("16.20")
    assert [(s.code, s.description) for s in ground_list.surcharges] == [("375", "Fuel surcharge")]


def test_single_service_object_response(ups, shipment):
    quotes = ups.rate(shipment, service="03", include_list_rates=True)
    assert ups.transport.requests[-1].url.endswith("/Rate")
    assert json.loads(ups.transport.requests[-1].body)["RateRequest"]["Shipment"]["Service"] == {"Code": "03"}
    assert sorted(q.total_charge for q in quotes) == [Decimal("13.48"), Decimal("16.20")]


def test_time_in_transit(clock, shipment):
    client = UPSClient(CREDS, transit=True, transport=MixedFixtures())
    body = client.build_rate_request(shipment)["RateRequest"]["Shipment"]
    assert body["DeliveryTimeInformation"]["Pickup"]["Date"] == "20260928"
    assert body["ShipmentTotalWeight"]["Weight"] == "5"
    (q,) = client.rate(shipment)
    assert q.transit_days == 4 and q.delivery_date == date(2026, 10, 2)


def test_token_error(ups, shipment):
    ups.transport = MixedFixtures({"ups_oauth": ["ups_oauth_denied"]})
    with pytest.raises(AuthError) as info:
        ups.rate(shipment)
    assert info.value.details[0].code == "10401"


def test_rejected_token_is_refreshed_once(ups, shipment):
    ups.transport = MixedFixtures({"ups_shop": ["ups_shop_badauth"]})
    quotes = ups.rate(shipment)
    assert quotes
    names = [r.url.rsplit("/", 1)[-1] for r in ups.transport.requests]
    assert names == ["token", "Shop", "token", "Shop"]


def test_persistent_auth_failure_raises(ups, shipment):
    ups.transport = MixedFixtures({"ups_shop": ["ups_shop_badauth", "ups_shop_badauth"]})
    with pytest.raises(AuthError):
        ups.rate(shipment)


def test_non_json_server_error(ups, shipment):
    ups.transport = MixedFixtures({"ups_shop": ["ups_shop_server_error"]})
    with pytest.raises(CarrierAPIError) as info:
        ups.rate(shipment)
    assert type(info.value) is CarrierAPIError and info.value.status == 503
    assert "Service Unavailable" in str(info.value)
