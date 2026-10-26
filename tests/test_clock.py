import pytest

from bridge_island.core.clock import Scheduler


def test_events_run_in_time_then_schedule_order() -> None:
    s, seen = Scheduler(), []
    s.at(2.0, lambda: seen.append("b"))
    s.at(1.0, lambda: seen.append("a"))
    s.at(2.0, lambda: seen.append("c"))
    s.run_until(5.0)
    assert seen == ["a", "b", "c"] and s.now == 5.0


def test_every_has_no_drift() -> None:
    s, times = Scheduler(), []
    s.every(0.05, lambda: times.append(s.now))
    s.run_until(1.0)
    assert len(times) == 21 and times[-1] == 1.0


def test_cannot_schedule_in_the_past() -> None:
    s = Scheduler()
    s.run_until(1.0)
    with pytest.raises(ValueError):
        s.at(0.5, lambda: None)


def test_seed_is_deterministic() -> None:
    assert Scheduler(7).rng.random() == Scheduler(7).rng.random()
    assert Scheduler(7).rng.random() != Scheduler(8).rng.random()


def test_after_is_relative_to_now() -> None:
    s, seen = Scheduler(), []
    s.at(1.0, lambda: s.after(0.5, lambda: seen.append(s.now)))
    s.run_until(3.0)
    assert seen == [1.5]
