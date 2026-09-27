from decimal import Decimal

import pytest

from carrier_rates import Address, Package, Shipment
from carrier_rates.units import ceil_to, dim_weight_lb, lb_to_kg, parse_dims, to_inches, to_lb


def test_weight_conversions():
    assert to_lb(16, "oz") == Decimal("1")
    assert to_lb("1", "kg").quantize(Decimal("0.001")) == Decimal("2.205")
    assert lb_to_kg(to_lb("3.5", "kg")).quantize(Decimal("0.0001")) == Decimal("3.5000")
    with pytest.raises(ValueError):
        to_lb(1, "stone")


def test_length_and_dims_parsing():
    assert to_inches("2.54", "cm").quantize(Decimal("0.0001")) == Decimal("1.0000")
    assert parse_dims("12x10x8") == (Decimal(12), Decimal(10), Decimal(8))
    assert parse_dims(" 12.5 X 10 * 8 ") == (Decimal("12.5"), Decimal(10), Decimal(8))
    with pytest.raises(ValueError):
        parse_dims("12x10")


def test_rounding_and_dim_weight():
    assert ceil_to(Decimal("4.01"), "0.1") == Decimal("4.1")
    assert ceil_to(Decimal("7.0"), "1") == Decimal("7")
    assert dim_weight_lb(12, 10, 8) == Decimal(7)  # 960 / 139 = 6.9 -> 7


def test_float_input_does_not_leak_binary_noise():
    assert Package.create(0.1, (1, 1, 1)).weight_lb == Decimal("0.1")


def test_package_from_metric():
    pkg = Package.create("2", ("30", "20", "10"), weight_unit="kg", dim_unit="cm")
    assert pkg.weight_lb.quantize(Decimal("0.01")) == Decimal("4.41")
    assert pkg.length_in.quantize(Decimal("0.01")) == Decimal("11.81")


def test_validation():
    with pytest.raises(ValueError):
        Package(Decimal(0), Decimal(1), Decimal(1), Decimal(1))
    with pytest.raises(ValueError):
        Address("")
    with pytest.raises(ValueError):
        Address("10001", "USA")
    with pytest.raises(ValueError):
        Shipment(Address("1"), Address("2"), ())
    assert Address("10001", "us").country_code == "US"


def test_total_weight(shipment):
    two = Shipment(shipment.origin, shipment.destination, shipment.packages * 2)
    assert two.total_weight_lb == Decimal(10)
