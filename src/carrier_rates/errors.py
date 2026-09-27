"""Exception hierarchy. Every carrier failure is mapped onto one of these."""

from __future__ import annotations

from dataclasses import dataclass


class CarrierRatesError(Exception):
    """Base class for everything this library raises."""


class ConfigError(CarrierRatesError):
    """Missing or invalid configuration (credentials, environment name, ...)."""


class TransportError(CarrierRatesError):
    """The HTTP request never produced a response (DNS, timeout, refused)."""


@dataclass(frozen=True)
class ErrorDetail:
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" if self.code else self.message


class CarrierAPIError(CarrierRatesError):
    """The carrier answered with an error payload."""

    def __init__(self, carrier: str, status: int, details: list[ErrorDetail] | None = None,
                 message: str | None = None):
        self.carrier = carrier
        self.status = status
        self.details = list(details or [])
        text = message or "; ".join(str(d) for d in self.details) or f"HTTP {status}"
        super().__init__(f"{carrier} HTTP {status}: {text}")


class AuthError(CarrierAPIError):
    """Credentials rejected, token expired or missing permission."""


class ValidationError(CarrierAPIError):
    """The carrier rejected the request content (bad postal code, weight, ...)."""
