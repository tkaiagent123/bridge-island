"""Tests written to kill the mutants that survived the first mutation run.

Each one pins down behaviour that earlier tests only exercised, without checking it exactly:
reference constants, validation boundaries, exact counters, health thresholds, immutability.
"""

import dataclasses
import math
from collections import Counter
from typing import Any

import pytest
import world

from bridge_island.age import age_profile
from bridge_island.analysis.change_impact import (
    Change,
    Impact,
    impact_bridge,
    impact_p2p,
    monte_carlo,
    summarize,
)
from bridge_island.baseline.platform import BaselinePlatform
from bridge_island.bridge.cyber import CyberMonitor
from bridge_island.bridge.health import HealthMonitor
from bridge_island.bridge.translator import Delivery
from bridge_island.bridge.uid import estimate
from bridge_island.codegen import Dictionary, load_module
from bridge_island.core import units
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import DataElement, Datum, Message, MessageSpec, Representation
from bridge_island.core.events import EventLog
from bridge_island.core.links import Link
from bridge_island.core.transforms import (
    BamDecode,
    BamEncode,
    BitPack,
    BitUnpack,
    Chain,
    EnumMapping,
    Identity,
    ScaleOffset,
    TransformError,
    UnitConversion,
    ValidityCheck,
    check_type,
    from_spec,
)
from bridge_island.protocols.command_response import CommandResponseBus, RemoteTerminal
from bridge_island.scenario import (
    MAX_DURATION_S,
    ScenarioError,
    load_scenario,
    parse_scenario,
    parse_scenario_text,
)
from bridge_island.system import build_bridged
from bridge_island.unique.subsystem import Need, Output

# --- the unit table matches independent definitions ------------------------------------------

# unit -> (base unit of its dimension, scale, offset): base = value * scale + offset
REFERENCE = {
    "ft": ("m", 0.3048, 0.0),
    "km": ("m", 1000.0, 0.0),
    "nmi": ("m", 1852.0, 0.0),
    "kt": ("m/s", 1852.0 / 3600.0, 0.0),
    "km/h": ("m/s", 1.0 / 3.6, 0.0),
    "rad": ("deg", 180.0 / math.pi, 0.0),
    "psi": ("kPa", 6.894757293168361, 0.0),
    "mmHg": ("kPa", 0.133322387415, 0.0),  # 133.322387415 Pa
    "degF": ("degC", 5.0 / 9.0, -160.0 / 9.0),
    "L/h": ("mL/h", 1000.0, 0.0),
    "L": ("mL", 1000.0, 0.0),
    "ms": ("s", 0.001, 0.0),
}


@pytest.mark.parametrize("unit", sorted(REFERENCE))
def test_every_unit_matches_its_definition_in_both_directions(unit: str) -> None:
    base, scale, offset = REFERENCE[unit]
    got_scale, got_offset = units.coefficients(unit, base)
    assert got_scale == pytest.approx(scale, rel=1e-12) and got_offset == pytest.approx(
        offset, abs=1e-12
    )
    back_scale, back_offset = units.coefficients(base, unit)
    assert back_scale == pytest.approx(1 / scale, rel=1e-12)
    assert back_offset == pytest.approx(-offset / scale, abs=1e-12)


def test_well_known_conversions_by_value() -> None:
    assert units.convert(1.0, "nmi", "m") == 1852.0
    assert units.convert(1.0, "kt", "m/s") == pytest.approx(0.514444, abs=1e-6)
    assert units.convert(760.0, "mmHg", "kPa") == pytest.approx(101.325, abs=1e-3)
    assert units.convert(14.6959, "psi", "kPa") == pytest.approx(101.325, abs=1e-3)
    assert units.convert(32.0, "degF", "degC") == pytest.approx(0.0, abs=1e-12)


# --- transform validation boundaries -----------------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        {"bam_decode": 32},
        {"bam_decode": 1},
        {"bit_unpack": [["a", 64]]},
        {"bit_pack": [["a", 1], ["b", 63]]},
        {"scale_offset": {"scale": 2}},
        {"scale_offset": {"scale": 2, "offset": 1, "to_units": "m"}},
        {"valid_range": [1, 1]},
        {"enum": {"map": {"a": "b"}, "to_encoding": "uint8"}},
    ],
)
def test_boundary_values_that_are_allowed(spec: Any) -> None:
    from_spec(spec)


@pytest.mark.parametrize(
    "spec",
    [
        {"bam_decode": 33},
        {"bam_decode": 0},
        {"bit_unpack": [["a", 65]]},
        {"bit_unpack": [["a", 32], ["b", 33]]},
        {"bit_pack": [["a", 0]]},
        {"bit_pack": []},
        {"bit_pack": "x"},
        {"bit_pack": [[1, 2]]},
        {"unit": [1, "m"]},
        {"unit": ["m", 2]},
        {"scale_offset": {"scale": 1, "to_units": 5}},
        {"scale_offset": {"offset": 1}},
        {"scale_offset": [1]},
        {"enum": {"map": {"a": 1}, "to_encoding": 5}},
        {"enum": {"map": {"a": None}}},
        {"enum": []},
        {"enum": "x"},
        {"enum": {"map": {"a": 1}, "bogus": 1}},
        {"valid_range": [True, 5]},
        {"valid_range": ["a", 5]},
        {"valid_range": [float("nan"), 5]},
        {"valid_range": [1, 2, 3]},
    ],
)
def test_values_just_outside_the_allowed_set_are_rejected(spec: Any) -> None:
    with pytest.raises(TransformError):
        from_spec(spec)


def test_transform_defaults_and_wrap_around() -> None:
    assert BamDecode().apply(16384) == 90.0 and BamEncode().apply(90.0) == 16384
    assert BamEncode(16).apply(360.0) == 0 and BamEncode(16).apply(359.999) == 0
    assert BamEncode(8).apply(360.0) == 0 and BamEncode(8).apply(180.0) == 128
    assert ScaleOffset(2.0).apply(3.0) == 6.0
    assert ValidityCheck(0, 10).apply(0) == 0 and ValidityCheck(0, 10).apply(10) == 10
    check_type(10**9, "uint")  # no width given: no range check
    check_type(65535, "uint16")
    with pytest.raises(Exception, match="uint16"):
        check_type(65536, "uint16")
    assert Identity().inverse() == Identity() and Chain(()).is_identity
    assert from_spec("identity").is_identity


# --- value objects are immutable --------------------------------------------------------------

REP = Representation("float64", "m", "f", "p")
SPEC = MessageSpec("M", (DataElement("e", REP),), 1.0, 1)
VALUE_OBJECTS = [
    REP,
    DataElement("e", REP),
    SPEC,
    Message("M", {}, 0.0),
    Datum("e", 1.0, 0.0),
    Link("a", "b", "p"),
    Need("n", REP, 1.0, 1.0, 1.0),
    Output("o", ()),
    Change("add", "M.e"),
    Impact(0, 0, 0, 0),
    Delivery("M.e", "S", Datum("e", 1.0, 0.0)),
    Dictionary(world.interface(1), {}),
    ScaleOffset(1.0),
    UnitConversion("ft", "m"),
    EnumMapping((("a", 1),)),
    BamDecode(),
    BamEncode(),
    BitUnpack((("a", 1),)),
    BitPack((("a", 1),)),
    ValidityCheck(0, 1),
    Chain(()),
    Identity(),
]


@pytest.mark.parametrize("obj", VALUE_OBJECTS, ids=lambda o: type(o).__name__)
def test_value_objects_cannot_be_modified(obj: Any) -> None:
    field = dataclasses.fields(obj)[0].name if dataclasses.fields(obj) else "x"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(obj, field, None)


# --- counters are exact, not merely positive --------------------------------------------------


def test_platform_emits_exactly_the_published_rate_even_if_started_twice() -> None:
    system = world.build(1, seed=1)
    system.platform.start()  # a second start must not double the schedule
    system.run(5.0)
    assert system.platform.emitted == {
        "NavigationState": 101,  # 20 Hz for 5 s, counting t = 0
        "AirData": 51,
        "AttitudeState": 251,
        "PowerStatus": 6,
        "TimeReference": 6,
    }


def test_the_first_message_is_valid_slightly_before_it_is_sent() -> None:
    sched = Scheduler()
    platform = BaselinePlatform(sched, world.interface(1), world.sources(1))
    assert platform.sample_age_s == 0.005
    spec = world.interface(1).published[0]
    sched.run_until(1.0)
    message = platform._emit(spec)
    assert message.source_time == pytest.approx(1.0 - 0.005)


def test_translator_counts_every_path_exactly() -> None:
    mapping = load_module(world.HERE / "generated" / "mapping_v1.py")
    per_message: dict[str, list[int]] = {}
    for ref, _, _, identity in mapping.DOWNLINKS:
        counts = per_message.setdefault(ref.split(".")[0], [0, 0])
        counts[0 if identity else 1] += 1
    system = world.build(1, seed=1)
    assert system.bridge is not None
    calls: Counter[str] = Counter()
    original = system.bridge.tm.down

    def counting(msg: Message) -> Any:
        calls[msg.spec_name] += 1
        return original(msg)

    system.bridge.tm.down = counting  # type: ignore[method-assign]
    system.run(5.0)
    tm = system.bridge.tm
    assert tm.identity_count == sum(calls[m] * c[0] for m, c in per_message.items()) > 0
    assert tm.translated_count == sum(calls[m] * c[1] for m, c in per_message.items()) > 0
    assert tm.rejected == 0


def test_uid_counts_every_send_exactly() -> None:
    system = world.build(1, seed=1, rate_policy="extrapolate")
    assert system.bridge is not None
    uid, sends = system.bridge.uid, Counter[str]()
    for name, port in uid._ports.items():
        original = port.send

        def counting(native: str, datum: Datum, name: str = name, original: Any = original) -> None:
            sends[name] += 1
            original(native, datum)

        port.send = counting  # type: ignore[method-assign]
    system.run(5.0)
    assert uid.sent + uid.estimates == sum(sends.values()) > 0
    assert uid.estimates > 0 and uid.decimated > 0


def test_downlink_validity_failures_are_rejected_logged_and_counted_per_element() -> None:
    sources = dict(world.sources(1))
    nav = sources["NavigationState"]
    sources["NavigationState"] = lambda t: {**nav(t), "latitude": 95.0}  # outside [-90, 90]
    mapping = load_module(world.HERE / "generated" / "mapping_v1.py")
    system = build_bridged(world.interface(1), sources, mapping, world.make_subsystems, 1, "GHAV")
    assert system.bridge is not None
    calls = Counter[str]()
    original = system.bridge.tm.down

    def counting(msg: Message) -> Any:
        calls[msg.spec_name] += 1
        return original(msg)

    system.bridge.tm.down = counting  # type: ignore[method-assign]
    system.run(3.0)
    tm, log = system.bridge.tm, system.bridge.log
    assert calls["NavigationState"] > 0
    assert tm.rejected == calls["NavigationState"] == len(log.of_kind("tm.reject"))
    assert "95.0 outside [-90.0, 90.0]" in log.of_kind("tm.reject")[0].detail
    display = system.subsystems["PlanningDisplay"]
    assert display.latest("nav_lat_deg") is None  # the bad element is withheld ...
    assert display.latest("nav_lon_deg") is not None  # ... and its neighbours are not


def test_translator_rejections_on_the_uplink_are_counted() -> None:
    system = world.build(1, seed=1)
    assert system.bridge is not None
    sender = system.subsystems["PlanningDisplay"]
    for values in (
        {"page": "MAP", "priority": 9},
        {"page": "NOPE", "priority": 1},
        {"page": "MAP"},
    ):
        sender.send("show_page", values)
    sender.send("show_page", {"page": "MAP", "priority": 1})  # a good one
    system.run(2.0)
    assert system.bridge.tm.rejected == 3 == len(system.bridge.log.of_kind("tm.reject_up"))
    assert len(system.platform.accepted) == 1


def test_bus_addresses_start_at_one_and_count_up() -> None:
    bus = CommandResponseBus(Scheduler())
    terminals = [RemoteTerminal(f"rt{i}") for i in range(3)]
    assert [bus.attach(t) for t in terminals] == [1, 2, 3]
    assert [t.address for t in terminals] == [1, 2, 3]


# --- the health monitor's exact thresholds ----------------------------------------------------


def health() -> tuple[HealthMonitor, Scheduler, EventLog, list[tuple[str, str, str]]]:
    sched, log, heard = Scheduler(), EventLog(), []
    monitor = HealthMonitor(sched, log, world.interface(1), lambda *a: heard.append(a))
    return monitor, sched, log, heard


def kinds(log: EventLog, subject: str = "") -> list[str]:
    return [e.kind for e in log.events if subject in e.detail]


def test_a_gap_is_a_discontinuity_only_beyond_three_periods() -> None:
    monitor, _, log, heard = health()
    nav = world.interface(1).published[0]
    assert nav.rate_hz == 20  # three periods = 0.15 s
    monitor.observe(Message("NavigationState", {}, 0.0))
    monitor.observe(Message("NavigationState", {}, 0.15))  # exactly three periods: fine
    assert log.events == []
    monitor.observe(Message("NavigationState", {}, 0.15 + 0.1501))
    assert kinds(log) == ["health.discontinuity"]
    assert heard[0][:2] == ("NavigationState", "discontinuity")
    assert heard[0][2].startswith("NavigationState: ")  # the annunciation says what it is about


def test_ordinary_traffic_raises_no_events_and_going_backwards_does() -> None:
    monitor, _, log, _ = health()
    for k in range(40):
        monitor.observe(Message("NavigationState", {}, k * 0.05))
    monitor.observe(
        Message("NavigationState", {}, 39 * 0.05)
    )  # the same time again: not out of order
    assert log.events == []
    monitor.observe(Message("NavigationState", {}, 39 * 0.05 - 0.001))
    assert kinds(log) == ["health.out_of_order"]
    monitor.observe(Message("Unpublished", {}, 0.0))  # unknown messages are ignored
    assert len(log.events) == 1


def test_data_is_stale_only_after_three_periods_and_recovery_is_reported() -> None:
    monitor, sched, log, heard = health()
    monitor.observe(Message("NavigationState", {}, 0.0))  # arrives at t = 0
    sched.run_until(0.15)
    monitor.scan()
    assert "health.stale" not in kinds(log, "NavigationState")  # exactly three periods old
    sched.run_until(0.1501)
    monitor.scan()
    assert kinds(log, "NavigationState") == ["health.stale"]
    monitor.scan()
    assert kinds(log, "NavigationState") == ["health.stale"]  # reported once
    monitor.observe(Message("NavigationState", {}, 0.2))
    # 0.2 s after the first message is also a source-time jump, so the gap is reported too
    assert kinds(log, "NavigationState") == [
        "health.stale",
        "health.discontinuity",
        "health.recovered",
    ]
    assert any(h[1] == "stale" and h[2].startswith("NavigationState: no data for") for h in heard)


def test_a_missed_health_scan_is_reported_after_twice_the_period() -> None:
    monitor, sched, log, _ = health()
    sched.run_until(1.0)
    monitor.scan()
    sched.run_until(1.2)
    monitor.scan()  # exactly two periods later: on time
    assert "health.bridge_stall" not in kinds(log)
    sched.run_until(1.45)  # 0.25 s later: late
    monitor.scan()
    stalls = [e for e in log.events if e.kind == "health.bridge_stall"]
    assert len(stalls) == 1 and "0.250" in stalls[0].detail


def test_only_the_systems_that_use_a_message_hear_about_it() -> None:
    system = world.build(1, seed=1)
    assert system.bridge is not None
    system.bridge.stall(2.2, 1.5)
    system.run(6.0)
    heard = {n: " ".join(d for _, _, d in s.annunciations) for n, s in system.subsystems.items()}
    assert (
        "NavigationState:" in heard["PlanningDisplay"]
        and "NavigationState:" in heard["CueingProcessor"]
    )
    assert "NavigationState:" not in heard["VoiceRelay"]  # it uses only the time and power messages
    assert all(
        "bridge:" in text for text in heard.values()
    )  # a stalled Bridge is everyone's business


def test_cyber_policy_is_exact() -> None:
    sched, log = Scheduler(), EventLog()
    cyber = CyberMonitor(sched, log, [("M.a", "S1")], [("S1", "msg")])
    assert cyber.check_down("M.a", "S1") and cyber.check_up("S1", "msg")
    assert not cyber.check_down("M.a", "S2") and not cyber.check_down("M.b", "S1")
    assert not cyber.check_up("S2", "msg") and not cyber.check_up("S1", "other")
    assert cyber.blocked == 4 and len(log.of_kind("cyber.block")) == 4


# --- estimates ---------------------------------------------------------------------------------


def test_defaults_and_edges_of_the_estimator() -> None:
    assert estimate("uint8", "1", [(1.0, 3)], now=1.2) == (
        False,
        None,
    )  # one sample: no default window
    assert estimate("float64", "deg", [(0.0, 0.0), (0.05, 180.0)], now=0.06, limit=1.0)[
        0
    ]  # exactly half a turn
    assert not estimate("float64", "deg", [(0.0, 0.0), (0.05, 180.5)], now=0.06, limit=1.0)[0]
    assert estimate("float64", "rad", [(0.0, 0.0), (0.05, math.pi)], now=0.06, limit=1.0)[0]
    assert estimate("float64", "m", [(1.0, 5.0), (1.0, 6.0)], now=1.01, limit=1.0) == (False, None)


# --- scenarios: exact limits ------------------------------------------------------------------


def test_scenario_limits_are_inclusive_where_stated() -> None:
    parse_scenario({"example": "aviation", "duration_s": MAX_DURATION_S})
    with pytest.raises(ScenarioError, match="at most"):
        parse_scenario({"example": "aviation", "duration_s": MAX_DURATION_S + 1})
    short = {"example": "aviation", "duration_s": 5, "events": [{"at": 1, "stall": 0.5}]}
    assert parse_scenario(short).events[0]["stall"] == 0.5  # a short stall is fine
    with pytest.raises(ScenarioError, match="stall"):
        parse_scenario({**short, "events": [{"at": 1, "stall": 0}]})
    assert parse_scenario({"example": "aviation", "duration_s": 1}).seed == 1  # the default seed
    assert parse_scenario(
        {"example": "aviation", "duration_s": 5, "events": [{"at": 5, "stall": 1}]}
    )


@pytest.mark.parametrize("sender", ['""', "5", "[a]"])
def test_a_sender_must_be_a_non_empty_name(sender: str) -> None:
    send = f"{{from: {sender}, message: m, values: {{}}}}"
    text = f"example: aviation\nduration_s: 5\nevents:\n  - {{at: 1, send: {send}}}\n"
    with pytest.raises(ScenarioError, match="non-empty text"):
        parse_scenario_text(text)


def test_an_unreadable_scenario_file_says_so(tmp_path: Any) -> None:
    binary = tmp_path / "s.yaml"
    binary.write_bytes(b"\xff\xfe\x00 not text")
    with pytest.raises(ScenarioError, match="not readable as UTF-8 text"):
        load_scenario(binary)
    with pytest.raises(ScenarioError, match="cannot read"):
        load_scenario(tmp_path / "missing.yaml")


# --- analysis: details the headline numbers do not exercise ------------------------------------


def test_a_rate_change_alone_costs_reverification_but_not_code() -> None:
    edges = {("M.a", "S1"), ("N.b", "S2")}
    changes = [Change("rate", "M")]
    assert impact_p2p(changes, edges) == Impact(0, 0, 0, 1)  # S1 is re-verified, no code
    assert impact_p2p([Change("rate", "X")], edges) == Impact(0, 0, 0, 0)
    assert impact_bridge(changes, edges) == Impact(0, 0, 0, 1)  # the Bridge itself


def test_percentiles_use_the_stated_definition() -> None:
    rows = [{"x": float(v)} for v in range(1, 101)]
    assert summarize(rows, "x") == {"mean": 50.5, "p5": 6.0, "p50": 51.0, "p95": 96.0}
    assert summarize([{"x": 7.0}], "x") == {"mean": 7.0, "p5": 7.0, "p50": 7.0, "p95": 7.0}


def test_monte_carlo_rows_are_rounded_and_in_range() -> None:
    for row in monte_carlo(3, trials=60):
        assert row["density"] == round(row["density"], 3) and 0.1 <= row["density"] <= 0.6
        assert 10 <= row["n"] <= 100 and 3 <= row["m"] <= 20 and 1 <= row["changes"] <= 6


# --- age ---------------------------------------------------------------------------------------


def test_deliveries_are_ordered_by_arrival_not_by_age_of_content() -> None:
    a = age_profile([(2.0, 0.1), (1.0, 0.9)], end=3.0)  # arrival order differs from content order
    b = age_profile([(1.0, 0.9), (2.0, 0.1)], end=3.0)
    assert a == b and a is not None
    peak = a[1]
    assert peak == pytest.approx(3.0 - 0.9)  # the 0.1 datum that came later changes nothing


def test_short_observation_windows_are_integrated_not_approximated() -> None:
    # two deliveries 0.5 s apart; age ramps 0.1 -> 0.6, then 0.1 again after the second arrives
    average, peak = age_profile([(1.0, 0.9), (1.5, 1.4)], end=1.5) or (0, 0)
    assert average == pytest.approx(0.35) and peak == pytest.approx(0.6)
    assert age_profile([(1.0, 0.5)], end=1.0) == (pytest.approx(0.5), pytest.approx(0.5))
