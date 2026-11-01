"""One test per defect found in the external review, so each fix stays fixed."""

import math

import pytest
import world

from bridge_island.baseline.platform import BaselinePlatform
from bridge_island.bridge import Bridge, InterfaceMismatch
from bridge_island.bridge.uid import estimate
from bridge_island.codegen import CodegenError, generate, load_module
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Datum, Representation
from bridge_island.core.transforms import TransformError, UnitConversion
from bridge_island.unique.subsystem import Need, UniqueSubsystem

# --- Bridge.start refuses a half-wired system instead of crashing later ----------------------


def wired(attach_baseline: bool = True, leave_out: str | None = None) -> Bridge:
    sched = Scheduler()
    mapping = load_module(world.HERE / "generated" / "mapping_v1.py")
    bridge = Bridge(sched, world.interface(1), mapping)  # type: ignore[arg-type]
    if attach_baseline:
        bridge.attach_baseline(BaselinePlatform(sched, world.interface(1), world.sources(1)))
    for name, sub in world.make_subsystems(sched).items():
        if name != leave_out:
            bridge.attach_unique(sub)
    return bridge


def test_start_names_the_subsystem_that_was_never_attached() -> None:
    with pytest.raises(InterfaceMismatch, match=r"not attached: \['VoiceRelay'\]"):
        wired(leave_out="VoiceRelay").start()


def test_start_needs_a_baseline() -> None:
    with pytest.raises(InterfaceMismatch, match="attach_baseline"):
        wired(attach_baseline=False).start()


def test_a_fully_wired_bridge_starts() -> None:
    wired().start()


# --- estimates: when to make one, and when not to --------------------------------------------

FLOAT = "float64"


def test_a_float_is_extrapolated_linearly_from_two_samples() -> None:
    ok, value = estimate(FLOAT, "m", [(0.0, 10.0), (1.0, 12.0)], now=1.5, limit=1.0)
    assert ok and value == pytest.approx(13.0)


def test_a_float_is_not_estimated_from_a_single_sample() -> None:
    assert estimate(FLOAT, "m", [(1.0, 5.0)], now=1.01, limit=1.0) == (False, None)


def test_integer_codes_and_enums_are_held() -> None:
    assert estimate("uint8", "1", [(1.0, 3)], now=1.2, limit=1.0) == (True, 3)
    assert estimate("enum", "1", [(0.0, "RUN"), (1.0, "HOLD")], now=1.5, limit=1.0) == (
        True,
        "HOLD",
    )
    assert estimate("bool", "1", [(0.0, True)], now=0.4, limit=1.0) == (True, True)


def test_no_estimate_after_the_limit_so_a_dead_source_is_not_masked() -> None:
    history = [(0.0, 1.0), (0.1, 2.0)]
    assert estimate(FLOAT, "m", history, now=0.1 + 0.15, limit=0.15)[0]
    assert not estimate(FLOAT, "m", history, now=0.1 + 0.151, limit=0.15)[0]
    assert estimate(FLOAT, "m", history, now=0.1 + 0.15)[0]  # default: 1.5 source periods
    assert not estimate(FLOAT, "m", history, now=0.1 + 0.16)[0]


def test_nothing_is_estimated_across_an_angle_wrapping_round() -> None:
    # 359.0 deg then 1.0 deg: a straight line would say about -7000 deg per second.
    assert estimate(FLOAT, "deg", [(0.0, 359.0), (0.05, 1.0)], now=0.06, limit=0.5) == (False, None)
    assert estimate(FLOAT, "rad", [(0.0, 6.2), (0.05, 0.05)], now=0.06, limit=0.5) == (False, None)
    ok, value = estimate(FLOAT, "deg", [(0.0, 10.0), (0.05, 11.0)], now=0.1, limit=0.5)
    assert ok and value == pytest.approx(12.0)  # a normal change of angle is extrapolated
    assert estimate(FLOAT, "m", [(0.0, 359.0), (0.05, 1.0)], now=0.06, limit=0.5)[0]  # not an angle


def test_time_going_backwards_gives_no_slope() -> None:
    assert estimate(FLOAT, "m", [(1.0, 5.0), (1.0, 6.0)], now=1.1, limit=1.0) == (False, None)
    assert estimate(FLOAT, "m", [(2.0, 5.0), (1.0, 6.0)], now=1.1, limit=1.0) == (False, None)


# --- smaller defects --------------------------------------------------------------------------


def test_unit_conversions_validate_their_units_where_they_are_written() -> None:
    with pytest.raises(TransformError, match="unknown unit"):
        UnitConversion("ft", "furlong")
    with pytest.raises(TransformError, match="cannot convert"):
        UnitConversion("ft", "kt")
    assert UnitConversion("ft", "m").apply(1.0) == pytest.approx(0.3048)


def test_a_subsystem_cannot_declare_two_inputs_with_one_name() -> None:
    rep = Representation("float64", "m", "f", "x")
    need = Need("v", rep, 1.0, 1.0, 1.0)
    with pytest.raises(ValueError, match="same native name"):
        UniqueSubsystem(Scheduler(), "S", "pubsub", (need, need))


def test_latest_is_the_most_recent_per_input() -> None:
    sched = Scheduler()
    rep = Representation("float64", "m", "f", "x")
    sub = UniqueSubsystem(sched, "S", "pubsub", (Need("a", rep, 1, 1, 1), Need("b", rep, 1, 1, 1)))
    assert sub.latest("a") is None
    for t, name, v in ((0.0, "a", 1.0), (0.1, "b", 2.0), (0.2, "a", 3.0)):
        sched.at(t, lambda name=name, v=v, t=t: sub.on_data(name, Datum(name, v, t)))
    sched.run_until(1.0)
    latest_a, latest_b = sub.latest("a"), sub.latest("b")
    assert latest_a is not None and latest_a.datum.value == 3.0
    assert latest_b is not None and latest_b.datum.value == 2.0
    assert sub.latest("nope") is None and sub.need("nope") is None


def test_a_field_mapped_twice_in_an_uplink_is_rejected(spec_v1, tmp_path) -> None:  # type: ignore[no-untyped-def]
    text = spec_v1.read_text(encoding="utf-8").replace(
        "      - from: priority\n        to: priority\n",
        "      - from: priority\n        to: priority\n"
        "        transforms: [{valid_range: [0, 3]}]\n"
        "      - from: priority\n        to: priority\n",
    )
    path = tmp_path / "s.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CodegenError, match="exactly once"):
        generate(path, world.dictionary(1))


def test_a_failed_build_leaves_no_half_written_module(spec_v1, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from bridge_island.codegen import generate_file

    bad = tmp_path / "bad.yaml"
    bad.write_text(
        spec_v1.read_text(encoding="utf-8").replace("[ft, m]", "[ft, kt]", 1), encoding="utf-8"
    )
    out = tmp_path / "out" / "m.py"
    with pytest.raises(CodegenError):
        generate_file(bad, world.dictionary(1), out)
    assert not out.exists()


def test_the_first_published_values_are_not_from_negative_time() -> None:
    """The 5 ms sample age puts the very first validity time before t = 0. That is deliberate
    (the datum was sampled before the run started) and must not leak into consumers' ages."""
    system = world.build(1, seed=1)
    system.run(1.0)
    for sub in system.subsystems.values():
        for r in sub.received:
            assert r.age >= 0 and math.isfinite(r.age)
