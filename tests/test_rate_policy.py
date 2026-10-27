"""Rate policy: 'cap' (default) versus 'extrapolate' when a subsystem can use a higher rate."""

import pytest
import world

from bridge_island.bridge.uid import UniqueInterfaceDistributor
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Datum, Representation
from bridge_island.core.events import EventLog
from bridge_island.unique.subsystem import Need, UniqueSubsystem


def run(policy: str, seconds: float = 4.0, **opts: object) -> world.System:
    system = world.build(1, seed=1, rate_policy=policy, **opts)
    system.run(seconds)
    return system


def received(system: world.System, sub: str, native: str) -> list:  # type: ignore[type-arg]
    return [r for r in system.subsystems[sub].received if r.native_name == native]


def test_cap_is_the_default_and_never_estimates() -> None:
    default = world.build(1, seed=1)
    default.run(4.0)
    assert default.bridge is not None and default.bridge.uid.rate_policy == "cap"
    assert default.bridge.uid.estimates == 0
    assert not any(r.datum.estimated for s in default.subsystems.values() for r in s.received)


def test_extrapolate_raises_the_delivered_rate_and_flags_every_estimate() -> None:
    cap, ext = run("cap"), run("extrapolate")
    real = received(cap, "CueingProcessor", "pos_alt_m")
    both = received(ext, "CueingProcessor", "pos_alt_m")
    assert len(both) > 2 * len(real)  # 20 Hz source delivered at close to the 50 Hz cap
    estimated = [r for r in both if r.datum.estimated]
    assert estimated and len(estimated) < len(both)  # real samples are still delivered as real
    assert all(r.datum.basis_time is not None for r in estimated)
    assert [r for r in both if not r.datum.estimated and r.datum.basis_time is not None] == []
    delivered = sum(1 for s in ext.subsystems.values() for r in s.received if r.datum.estimated)
    assert ext.bridge is not None
    assert 0 <= ext.bridge.uid.estimates - delivered <= 5  # the rest are still on the bus


def test_an_estimates_age_is_measured_from_the_real_sample_it_is_based_on() -> None:
    ext = run("extrapolate")
    for r in received(ext, "CueingProcessor", "pos_alt_m"):
        d = r.datum
        if d.estimated:
            assert d.basis_time is not None and d.basis_time < d.source_time
            assert r.age == pytest.approx(r.delivered_at - d.basis_time)
            assert r.age > r.delivered_at - d.source_time  # older than its own stamp suggests
        else:
            assert r.age == pytest.approx(r.delivered_at - d.source_time)


def test_estimates_are_close_to_the_truth_for_smooth_signals_and_labelled_synthetic() -> None:
    ext = run("extrapolate")
    nav = world.sources(1)["NavigationState"]
    errors = [
        abs(r.datum.value - nav(r.datum.source_time)["altitude"] * 0.3048)
        for r in received(ext, "CueingProcessor", "pos_alt_m")
        if r.datum.estimated
    ]
    assert errors and max(errors) < 0.01  # metres, for these invented, slowly varying signals


def test_non_float_encodings_are_held_not_extrapolated() -> None:
    ext = run("extrapolate", max_extrapolation_s=0.5)
    estimated = [r for r in received(ext, "VoiceRelay", "power_state") if r.datum.estimated]
    assert estimated and all(r.datum.value == 1 for r in estimated)  # integer code: held


def test_extrapolation_stops_after_the_limit_so_a_dead_source_is_not_masked() -> None:
    ext = run("extrapolate", max_extrapolation_s=0.1)
    for sub in ext.subsystems.values():
        for r in sub.received:
            if r.datum.estimated:
                assert r.delivered_at - r.datum.basis_time <= 0.1 + 0.01  # type: ignore[operator]
    wide = run("extrapolate", max_extrapolation_s=0.2)
    narrow_count = sum(1 for s in ext.subsystems.values() for r in s.received if r.datum.estimated)
    wide_count = sum(1 for s in wide.subsystems.values() for r in s.received if r.datum.estimated)
    assert narrow_count < wide_count


def test_no_estimates_are_delivered_while_the_bridge_is_stalled() -> None:
    system = world.build(1, seed=1, rate_policy="extrapolate")
    assert system.bridge is not None
    system.bridge.stall(at=2.0, duration=1.5)
    system.run(5.0)
    inside = [
        r
        for s in system.subsystems.values()
        for r in s.received
        if r.datum.estimated and 2.05 < r.delivered_at < 3.5
    ]
    assert inside == []
    assert any(
        r.datum.estimated and r.delivered_at > 3.5
        for s in system.subsystems.values()
        for r in s.received
    )


def test_estimates_survive_every_native_bus_type() -> None:
    sched = Scheduler()
    uid = UniqueInterfaceDistributor("uid", sched, EventLog(), lambda *_: None, "extrapolate", 0.5)
    rep = Representation("float64", "m", "f", "x")
    for protocol in ("pubsub", "label_words", "command_response"):
        sub = UniqueSubsystem(sched, protocol, protocol, (Need("v", rep, 20.0, 1.0, 1.0),))
        uid.connect(sub)
    for t in (0.0, 0.1):  # a source that moves 1 m per 0.1 s
        sched.at(t, lambda t=t: [uid.deliver(n, Datum("v", 10 * t, t)) for n in uid.subsystems])
    sched.run_until(0.5)
    for name, sub in uid.subsystems.items():
        est = [r for r in sub.received if r.datum.estimated]
        assert est, name
        for r in est:  # linear: value = 10 * time
            assert r.datum.value == pytest.approx(10 * r.datum.source_time, abs=1e-6)
            assert r.datum.basis_time == pytest.approx(0.1)


def test_unknown_policy_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown rate policy"):
        UniqueInterfaceDistributor("uid", Scheduler(), EventLog(), lambda *_: None, "interpolate")


def test_a_datums_age_uses_its_basis_only_when_estimated() -> None:
    assert Datum("x", 1.0, 5.0).age(6.0) == 1.0
    assert Datum("x", 1.0, 5.0, True, 4.0).age(6.0) == 2.0
    assert Datum("x", 1.0, 5.0, True, None).age(6.0) == 1.0


def test_neither_policy_meets_the_full_need_and_the_docs_say_so() -> None:
    """Numbers quoted in docs/concepts.md: 20 Hz source, subsystem wants 50 Hz and 50 ms."""

    def measure(policy: str) -> dict[str, float]:
        s = world.build(1, seed=1, rate_policy=policy)
        s.run(10.0)
        cue = s.subsystems["CueingProcessor"]
        alt = [r for r in cue.received if r.native_name == "pos_alt_m"]
        real = [r for r in alt if not r.datum.estimated]
        return {
            "per_s": len(alt) / 10,
            "estimated_share": sum(r.datum.estimated for r in alt) / len(alt),
            "real_age_ms": 1000 * sum(r.age for r in real) / len(real),
            "max_age_ms": 1000 * max(r.age for r in alt),
            "late": float(len(cue.late)),
        }

    cap, ext = measure("cap"), measure("extrapolate")
    assert cap["per_s"] == 20.0 and cap["late"] == 0  # real data, within budget, below the rate
    assert 49.0 <= ext["per_s"] <= 50.0  # the rate is met, never exceeded
    assert round(ext["estimated_share"], 1) == 0.6
    assert round(cap["real_age_ms"], 1) == 6.7 and round(ext["real_age_ms"], 1) == 21.7
    assert round(ext["max_age_ms"], 1) == 56.7 and ext["late"] > 0  # but the budget is missed
