"""A small explicit unit-conversion table (no external units library).

Every unit maps to a base unit of its dimension: ``base = value * scale + offset``.
"""

from __future__ import annotations

import math


class UnitError(ValueError):
    """Unknown unit, or a conversion between different dimensions."""


# unit -> (dimension, scale to base, offset to base)
_UNITS: dict[str, tuple[str, float, float]] = {
    "1": ("none", 1.0, 0.0),
    "m": ("length", 1.0, 0.0),
    "ft": ("length", 0.3048, 0.0),
    "km": ("length", 1000.0, 0.0),
    "nmi": ("length", 1852.0, 0.0),
    "m/s": ("speed", 1.0, 0.0),
    "kt": ("speed", 1852.0 / 3600.0, 0.0),
    "km/h": ("speed", 1.0 / 3.6, 0.0),
    "deg": ("angle", 1.0, 0.0),
    "rad": ("angle", 180.0 / math.pi, 0.0),
    "kPa": ("pressure", 1.0, 0.0),
    "psi": ("pressure", 6.894757293168361, 0.0),
    "mmHg": (
        "pressure",
        0.133322387415,
        0.0,
    ),  # conventional mercury: 13595.1 kg/m3 at standard gravity
    "degC": ("temperature", 1.0, 0.0),
    "degF": ("temperature", 5.0 / 9.0, -160.0 / 9.0),
    "mL/h": ("flow", 1.0, 0.0),
    "L/h": ("flow", 1000.0, 0.0),
    "mL": ("volume", 1.0, 0.0),
    "L": ("volume", 1000.0, 0.0),
    "s": ("time", 1.0, 0.0),
    "ms": ("time", 0.001, 0.0),
    "V": ("voltage", 1.0, 0.0),
}


def _lookup(unit: str) -> tuple[str, float, float]:
    try:
        return _UNITS[unit]
    except KeyError:
        raise UnitError(f"unknown unit: {unit!r}") from None


def coefficients(src: str, dst: str) -> tuple[float, float]:
    """Return ``(scale, offset)`` such that ``dst_value = src_value * scale + offset``."""
    if src == dst:
        _lookup(src)
        return 1.0, 0.0
    dim_s, scale_s, off_s = _lookup(src)
    dim_d, scale_d, off_d = _lookup(dst)
    if dim_s != dim_d:
        raise UnitError(f"cannot convert {src!r} ({dim_s}) to {dst!r} ({dim_d})")
    return scale_s / scale_d, (off_s - off_d) / scale_d


def convert(value: float, src: str, dst: str) -> float:
    scale, offset = coefficients(src, dst)
    return value * scale + offset
