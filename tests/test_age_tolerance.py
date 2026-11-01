"""Time spent with data older than the consumer tolerates (its own stale_after requirement)."""

import pytest
import world

from bridge_island.age import time_above
from bridge_island.scenario import parse_scenario, run_scenario
from bridge_island.summary import findings, summarize


def test_time_above_for_a_periodic_stream_matches_the_closed_form() -> None:
    # period 1 s, delay 0.1 s: age ramps 0.1 -> 1.1 between deliveries.
    deliveries = [(k + 0.1, float(k)) for k in range(10)]
    end = deliveries[-1][0]
    assert time_above(deliveries, end, threshold=2.0) == 0.0  # never that old
    assert time_above(deliveries, end, threshold=0.6) == pytest.approx(
        9 * 0.5
    )  # 0.5 s above per interval
    assert time_above(deliveries, end, threshold=0.0) == pytest.approx(end - deliveries[0][0])


def test_time_above_counts_silence_after_the_last_delivery() -> None:
    assert time_above([(1.0, 0.9)], end=11.0, threshold=2.0) == pytest.approx(
        11.0 - (0.9 + 2.0)
    )  # age reaches 2 s at t = 2.9
    assert time_above([], end=5.0, threshold=1.0) == 0.0


def run(duration: float, stall: float | None) -> dict:  # type: ignore[type-arg]
    events = [] if stall is None else [{"at": 2.0, "stall": stall}]
    sc = parse_scenario({"example": "aviation", "duration_s": duration, "events": events})
    system, _ = run_scenario(sc, world_module())
    return summarize(system)


def world_module():  # type: ignore[no-untyped-def]
    return world


def test_a_long_stall_is_reported_as_time_the_consumer_was_out_of_tolerance() -> None:
    quiet = run(10.0, None)
    assert all(
        s["stale_s"] == 0.0 and s["stale_input"] is None for s in quiet["subsystems"].values()
    )
    stalled = run(10.0, 4.0)  # PlanningDisplay tolerates 2 s of age: a 4 s stall exceeds it
    display = stalled["subsystems"]["PlanningDisplay"]
    assert display["stale_input"] is not None and display["stale_s"] == pytest.approx(
        2.2, abs=0.1
    )  # over 2 s from t = 3.8 until data resumes at 6.0
    cueing = stalled["subsystems"]["CueingProcessor"]  # tolerates only 0.25 s
    assert cueing["stale_s"] == pytest.approx(3.75, abs=0.3)
    text = " ".join(findings(stalled))
    assert "data was older than it tolerates for" in text


def test_the_old_arrival_only_metrics_missed_it() -> None:
    stalled = run(10.0, 4.0)
    display = stalled["subsystems"]["PlanningDisplay"]
    assert display["mean_age_ms"] < 100  # looked healthy at arrival
    assert display["peak_age_ms"] > 3500  # but the data it held got more than 3.5 s old
