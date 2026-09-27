"""Carrier-neutral inputs (Shipment/Package/Address) and output (RateQuote)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from .units import Number, to_decimal, to_inches, to_lb


@dataclass(frozen=True)
class Address:
    postal_code: str
    country_code: str = "US"
    state: str | None = None
    city: str | None = None
    residential: bool = False
    lines: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.postal_code or not self.postal_code.strip():
            raise ValueError("postal_code is required")
        if len(self.country_code) != 2:
            raise ValueError("country_code must be a 2-letter ISO code")
        object.__setattr__(self, "country_code", self.country_code.upper())


@dataclass(frozen=True)
class Package:
    """One box. Weight in pounds, dimensions in inches."""

    weight_lb: Decimal
    length_in: Decimal
    width_in: Decimal
    height_in: Decimal

    def __post_init__(self) -> None:
        for name in ("weight_lb", "length_in", "width_in", "height_in"):
            value = to_decimal(getattr(self, name))
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")
            object.__setattr__(self, name, value)

    @classmethod
    def create(cls, weight: Number, dims: tuple[Number, Number, Number], *,
               weight_unit: str = "lb", dim_unit: str = "in") -> "Package":
        length, width, height = (to_inches(d, dim_unit) for d in dims)
        return cls(to_lb(weight, weight_unit), length, width, height)


@dataclass(frozen=True)
class Shipment:
    origin: Address
    destination: Address
    packages: tuple[Package, ...]
    ship_date: date | None = None

    def __post_init__(self) -> None:
        pkgs = tuple(self.packages)
        if not pkgs:
            raise ValueError("a shipment needs at least one package")
        object.__setattr__(self, "packages", pkgs)

    @property
    def total_weight_lb(self) -> Decimal:
        return sum((p.weight_lb for p in self.packages), Decimal(0))


@dataclass(frozen=True)
class Surcharge:
    code: str
    description: str
    amount: Decimal


@dataclass(frozen=True)
class RateQuote:
    carrier: str
    service_code: str
    service_name: str
    total_charge: Decimal
    currency: str
    is_list_rate: bool
    billable_weight: Decimal | None = None
    billable_weight_unit: str | None = None
    transit_days: int | None = None
    delivery_date: date | None = None
    surcharges: tuple[Surcharge, ...] = field(default_factory=tuple)

    @property
    def rate_type(self) -> str:
        return "list" if self.is_list_rate else "account"

    def to_dict(self) -> dict[str, Any]:
        return {
            "carrier": self.carrier,
            "service_code": self.service_code,
            "service_name": self.service_name,
            "total_charge": _money(self.total_charge),
            "currency": self.currency,
            "rate_type": self.rate_type,
            "billable_weight": None if self.billable_weight is None else str(self.billable_weight),
            "billable_weight_unit": self.billable_weight_unit,
            "transit_days": self.transit_days,
            "delivery_date": self.delivery_date.isoformat() if self.delivery_date else None,
            "surcharges": [
                {"code": s.code, "description": s.description, "amount": _money(s.amount)}
                for s in self.surcharges
            ],
        }


def _money(value: Decimal) -> str:
    """Two decimal places unless the carrier sent more precision."""
    return f"{value:.2f}" if value == value.quantize(Decimal("0.01")) else str(value)
