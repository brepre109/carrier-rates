import json

import pytest

from carrier_rates.auth import TokenCache
from carrier_rates.errors import CarrierRatesError
from carrier_rates.sanitize import REDACTED, sanitize
from carrier_rates.transport import (FixtureTransport, HttpRequest, HttpResponse, RecordingTransport,
                                     fixture_name)
from conftest import FakeClock


def test_token_cache_expiry():
    clock = FakeClock(0)
    cache = TokenCache(skew=60, clock=clock)
    cache.put("k", "tok", expires_in=3600)
    clock.now = 3539
    assert cache.get("k") == "tok"
    clock.now = 3540  # inside the skew window -> treated as expired
    assert cache.get("k") is None
    cache.put("k", "tok2", "14399")  # UPS sends a string
    assert cache.get("k") == "tok2"
    cache.invalidate("k")
    assert cache.get("k") is None


def test_client_reuses_token_until_expiry(fedex, shipment, clock):
    fedex.rate(shipment)
    fedex.rate(shipment)
    paths = [r.path for r in fedex.transport.requests]
    assert paths.count("/oauth/token") == 1 and paths.count("/rate/v1/rates/quotes") == 2
    clock.now += 3600  # fixture token lives 3599 s
    fedex.rate(shipment)
    assert [r.path for r in fedex.transport.requests].count("/oauth/token") == 2


def test_shared_cache_is_keyed_per_carrier(fedex, ups, shipment):
    shared = TokenCache()
    fedex.token_cache = ups.token_cache = shared
    assert fedex.access_token() == "fixture-fedex-access-token"
    assert ups.access_token() == "fixture-ups-access-token"


def test_fixture_routing():
    assert fixture_name("https://apis.fedex.com/oauth/token") == "fedex_oauth"
    assert fixture_name("https://x/rate/v1/rates/quotes") == "fedex_rate"
    assert fixture_name("https://x/security/v1/oauth/token") == "ups_oauth"
    assert fixture_name("https://x/api/rating/v2409/Shoptimeintransit?additionalinfo=") == "ups_shoptimeintransit"
    with pytest.raises(CarrierRatesError):
        fixture_name("https://x/unknown")


def test_missing_fixture(tmp_path):
    with pytest.raises(CarrierRatesError, match="fixture not found"):
        FixtureTransport(tmp_path).send(HttpRequest("POST", "https://x/oauth/token"))


def test_sanitize_keys_and_literals():
    raw = {"access_token": "abc", "output": {"accountNumber": {"value": "12345"},
           "note": "billed to 12345", "rates": [{"amount": 1.5, "ShipperNumber": "X9"}]},
           "Shipper": {"Name": "Someone", "Address": {"AddressLine": ["1 Main St"], "PostalCode": "10001"}}}
    out = sanitize(raw, secrets=["12345"])
    assert out["access_token"] == REDACTED
    assert out["output"]["accountNumber"] == {"value": REDACTED}
    assert out["output"]["note"] == f"billed to {REDACTED}"
    assert out["output"]["rates"][0] == {"amount": 1.5, "ShipperNumber": REDACTED}
    assert out["Shipper"]["Name"] == REDACTED
    assert out["Shipper"]["Address"] == {"AddressLine": [REDACTED], "PostalCode": "10001"}
    assert raw["access_token"] == "abc"  # input untouched


class _FakeLive:
    def send(self, request):
        body = {"access_token": "live-secret-token", "expires_in": 3599,
                "output": {"note": "acct ACCT777", "rateReplyDetails": []}}
        return HttpResponse(200, json.dumps(body).encode(), {"Content-Type": "application/json"})


def test_recording_writes_replayable_sanitized_fixture(tmp_path):
    rec = RecordingTransport(_FakeLive(), tmp_path, secrets=["ACCT777"])
    resp = rec.send(HttpRequest("POST", "https://apis-sandbox.fedex.com/oauth/token"))
    assert json.loads(resp.body)["access_token"] == "live-secret-token"  # caller sees the real response
    text = (tmp_path / "fedex_oauth.json").read_text()
    assert "live-secret-token" not in text and "ACCT777" not in text
    replay = FixtureTransport(tmp_path).send(HttpRequest("POST", "https://apis.fedex.com/oauth/token"))
    assert json.loads(replay.body)["expires_in"] == 3599
