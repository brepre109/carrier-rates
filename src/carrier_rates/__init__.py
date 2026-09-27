"""carrier-rates: normalized FedEx and UPS rate quotes."""

from .errors import (
    AuthError,
    CarrierAPIError,
    CarrierRatesError,
    ConfigError,
    TransportError,
    ValidationError,
)
from .models import Address, Package, RateQuote, Shipment, Surcharge
from .service import QuoteResult, cheapest, quote_all

__version__ = "0.1.0"

__all__ = [
    "Address",
    "AuthError",
    "CarrierAPIError",
    "CarrierRatesError",
    "ConfigError",
    "Package",
    "QuoteResult",
    "RateQuote",
    "Shipment",
    "Surcharge",
    "TransportError",
    "ValidationError",
    "cheapest",
    "quote_all",
]
