"""Unit conversion helpers. Internally everything is pounds and inches."""

from __future__ import annotations

import math
import re
from decimal import ROUND_CEILING, Decimal
from typing import Union

Number = Union[int, float, str, Decimal]

LB_PER_KG = Decimal("2.20462262185")
LB_PER_OZ = Decimal("0.0625")
IN_PER_CM = Decimal("0.3937007874")

_WEIGHT = {"lb": Decimal(1), "lbs": Decimal(1), "kg": LB_PER_KG, "oz": LB_PER_OZ, "g": LB_PER_KG / 1000}
_LENGTH = {"in": Decimal(1), "cm": IN_PER_CM, "mm": IN_PER_CM / 10}


def to_decimal(value: Number) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # str() avoids binary float artefacts such as 0.1 -> 0.1000000000000000055
        return Decimal(str(value))
    return Decimal(value)


def to_lb(value: Number, unit: str = "lb") -> Decimal:
    try:
        factor = _WEIGHT[unit.lower()]
    except KeyError:
        raise ValueError(f"unknown weight unit {unit!r}; use one of {sorted(_WEIGHT)}") from None
    return to_decimal(value) * factor


def to_inches(value: Number, unit: str = "in") -> Decimal:
    try:
        factor = _LENGTH[unit.lower()]
    except KeyError:
        raise ValueError(f"unknown length unit {unit!r}; use one of {sorted(_LENGTH)}") from None
    return to_decimal(value) * factor


def lb_to_kg(value: Number) -> Decimal:
    return to_decimal(value) / LB_PER_KG


_DIMS_RE = re.compile(r"^\s*([\d.]+)\s*[xX*]\s*([\d.]+)\s*[xX*]\s*([\d.]+)\s*$")


def parse_dims(text: str, unit: str = "in") -> tuple[Decimal, Decimal, Decimal]:
    """Parse ``"12x10x8"`` into (length, width, height) in inches."""
    m = _DIMS_RE.match(text)
    if not m:
        raise ValueError(f"dimensions must look like LxWxH, got {text!r}")
    return tuple(to_inches(v, unit) for v in m.groups())  # type: ignore[return-value]


def ceil_to(value: Decimal, step: str) -> Decimal:
    """Round up to a step such as ``"1"`` or ``"0.1"``."""
    q = Decimal(step)
    return (value / q).to_integral_value(rounding=ROUND_CEILING) * q


def dim_weight_lb(length_in: Number, width_in: Number, height_in: Number, divisor: Number = 139) -> Decimal:
    """Dimensional weight estimate (L*W*H / divisor, rounded up to a whole pound).

    139 is the common US domestic divisor for both carriers' published rates; account
    contracts can differ, so the carrier-reported billable weight always wins.
    """
    cubic = to_decimal(length_in) * to_decimal(width_in) * to_decimal(height_in)
    return Decimal(math.ceil(cubic / to_decimal(divisor)))
