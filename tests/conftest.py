from datetime import date
from pathlib import Path

import pytest

from carrier_rates import Address, Package, Shipment
from carrier_rates.auth import TokenCache
from carrier_rates.config import CarrierCredentials
from carrier_rates.fedex import FedExClient
from carrier_rates.transport import FixtureTransport, bundled_fixture_dir
from carrier_rates.ups import UPSClient

TEST_FIXTURES = Path(__file__).parent / "fixtures"
CREDS = CarrierCredentials("test-client-id", "test-client-secret", "TESTACCT1")


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class MixedFixtures(FixtureTransport):
    """Bundled fixtures, plus error fixtures from tests/fixtures; a queue lets a test
    serve a different fixture on the first N calls to one route."""

    def __init__(self, queue=None):
        super().__init__(bundled_fixture_dir())
        self.queue = {k: list(v) for k, v in (queue or {}).items()}

    def send(self, request):
        from carrier_rates.transport import fixture_name
        name = fixture_name(request.url)
        if self.queue.get(name):
            override = self.queue[name].pop(0)
            self.requests.append(request)
            inner = FixtureTransport(TEST_FIXTURES)
            inner.overrides = {name: override}
            return inner.send(request)
        return super().send(request)


@pytest.fixture
def shipment():
    return Shipment(
        origin=Address("90210", "US", state="CA"),
        destination=Address("10001", "US", state="NY", residential=True),
        packages=(Package.create("5", ("12", "10", "8")),),
        ship_date=date(2026, 9, 28),
    )


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def fedex(clock):
    return FedExClient(CREDS, transport=MixedFixtures(), token_cache=TokenCache(clock=clock))


@pytest.fixture
def ups(clock):
    return UPSClient(CREDS, transport=MixedFixtures(), token_cache=TokenCache(clock=clock))
