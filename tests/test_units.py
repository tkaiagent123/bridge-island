import math

import pytest

from bridge_island.core import units


def test_known_conversions() -> None:
    assert units.convert(1000.0, "ft", "m") == pytest.approx(304.8)
    assert units.convert(100.0, "kt", "km/h") == pytest.approx(185.2)
    assert units.convert(180.0, "deg", "rad") == pytest.approx(math.pi)
    assert units.convert(212.0, "degF", "degC") == pytest.approx(100.0)
    assert units.convert(0.0, "degC", "degF") == pytest.approx(32.0)
    assert units.convert(2.0, "L/h", "mL/h") == pytest.approx(2000.0)


def test_same_unit_is_exact() -> None:
    assert units.coefficients("ft", "ft") == (1.0, 0.0)


def test_round_trip_every_pair_in_a_dimension() -> None:
    for a, b in [("ft", "nmi"), ("psi", "mmHg"), ("degF", "degC"), ("ms", "s")]:
        assert units.convert(units.convert(37.5, a, b), b, a) == pytest.approx(37.5)


def test_unknown_unit_and_dimension_mismatch() -> None:
    with pytest.raises(units.UnitError, match="unknown unit"):
        units.convert(1.0, "furlong", "m")
    with pytest.raises(units.UnitError, match="cannot convert"):
        units.convert(1.0, "ft", "kt")
    with pytest.raises(units.UnitError):
        units.coefficients("nope", "nope")
