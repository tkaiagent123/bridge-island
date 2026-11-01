"""Age-of-information metrics, checked against closed forms."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from bridge_island.age import age_profile
from bridge_island.core.elements import Datum
from bridge_island.summary import summarize
from bridge_island.unique.subsystem import Received


def periodic(period: float, delay: float, n: int) -> list[tuple[float, float]]:
    return [(k * period + delay, k * period) for k in range(n)]


@pytest.mark.parametrize("period, delay", [(1.0, 0.1), (0.05, 0.007), (2.0, 0.0), (0.5, 0.25)])
def test_a_periodic_stream_has_the_textbook_average_and_peak(period: float, delay: float) -> None:
    deliveries = periodic(period, delay, 50)
    average, peak = age_profile(deliveries, end=deliveries[-1][0]) or (math.nan, math.nan)
    assert average == pytest.approx(delay + period / 2)
    assert peak == pytest.approx(delay + period)


def test_age_keeps_growing_after_the_last_delivery() -> None:
    deliveries = periodic(1.0, 0.1, 3)  # last delivery at t = 2.1 with source time 2.0
    _, peak_at_last = age_profile(deliveries, end=2.1) or (0, 0)
    average, peak = age_profile(deliveries, end=7.1) or (0, 0)
    assert peak_at_last == pytest.approx(1.1)
    assert peak == pytest.approx(5.1)  # five more seconds of silence on top of the 0.1 delay
    assert average > 1.0


def test_an_older_datum_arriving_late_does_not_make_the_data_younger() -> None:
    in_order = age_profile([(1.0, 0.9), (3.0, 2.9)], end=4.0)
    with_stale = age_profile([(1.0, 0.9), (2.0, 0.1), (3.0, 2.9)], end=4.0)
    assert in_order == with_stale  # the stale one resets nothing


def test_degenerate_streams() -> None:
    assert age_profile([], end=5.0) is None
    assert age_profile([(2.0, 1.5)], end=2.0) == (pytest.approx(0.5), pytest.approx(0.5))
    average, peak = age_profile([(2.0, 1.5)], end=4.0) or (0, 0)
    assert average == pytest.approx(1.5) and peak == pytest.approx(2.5)  # 0.5 -> 2.5 over 2 s
    assert age_profile([(3.0, 2.0), (1.0, 0.5)], end=3.0) == age_profile(
        [(1.0, 0.5), (3.0, 2.0)], end=3.0
    )


@given(
    st.lists(st.tuples(st.floats(0, 100), st.floats(0, 100)), min_size=1, max_size=40),
    st.floats(0, 150),
)
def test_the_peak_is_never_below_the_average_and_ages_are_not_negative(
    pairs: list[tuple[float, float]], end: float
) -> None:
    deliveries = [(t, min(b, t)) for t, b in pairs]  # information cannot be from the future
    average, peak = age_profile(deliveries, end) or (0.0, 0.0)
    assert -1e-9 <= average <= peak + 1e-9


def test_estimates_are_aged_from_the_real_sample_they_are_based_on() -> None:
    estimate = Received("x", Datum("x", 1.0, source_time=5.0, estimated=True, basis_time=4.0), 5.0)
    assert estimate.age == pytest.approx(1.0)  # measured from the basis, not from its own stamp


def test_summary_reports_both_measures_per_subsystem() -> None:
    import world  # the aviation example, put on the path by conftest

    system = world.build(1, seed=1)
    system.run(6.0)
    summary = summarize(system)
    for name, s in summary["subsystems"].items():
        assert s["time_avg_age_ms"] is not None and s["peak_age_ms"] is not None, name
        assert s["mean_age_ms"] <= s["time_avg_age_ms"] + 1e-6  # holding data only makes it older
        assert s["time_avg_age_ms"] <= s["peak_age_ms"] + 1e-6
    logger = summary["subsystems"]["StatusLogger"]  # a 1 Hz subsystem: gaps dominate
    assert logger["time_avg_age_ms"] > 1.4 * logger["mean_age_ms"]
    assert logger["peak_age_ms"] > 1900  # about 2 s: a second of delay plus a second to the next
