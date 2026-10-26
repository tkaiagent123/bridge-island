import itertools
from dataclasses import replace

import pytest
import world

from bridge_island.baseline.platform import BaselinePlatform
from bridge_island.bridge import Bridge
from bridge_island.codegen import generate_file, load_module
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Datum, Message
from bridge_island.core.links import Network
from bridge_island.unique.subsystem import UniqueSubsystem


def kinds(system: world.System) -> list[str]:
    return [e.kind for e in system.bridge.log.events]


def test_unavailable_element_is_reported_not_silent(spec_v1, tmp_path):  # type: ignore[no-untyped-def]
    dic = world.dictionary(1)
    pub = tuple(
        replace(m, elements=tuple(e for e in m.elements if e.name != "baro_altitude_ft"))
        if m.name == "AirData"
        else m
        for m in dic.interface.published
    )
    iface = replace(dic.interface, published=pub)
    text = spec_v1.read_text().replace(
        "{from: AirData.baro_altitude_ft, to: CueingProcessor.baro_alt_m, "
        "transforms: [{unit: [ft, m]}]}",
        "{from: AirData.baro_altitude_ft, to: CueingProcessor.baro_alt_m, "
        "status: removed, reason: deprecated element removed}",
    )
    spec = tmp_path / "mapping_removed.yaml"
    spec.write_text(text)
    mapping = load_module(generate_file(spec, replace(dic, interface=iface), tmp_path / "m.py"))
    sched = Scheduler(1)
    src = dict(world.sources(1))
    src["AirData"] = lambda t: {"airspeed_kt": 240.0}
    platform = BaselinePlatform(sched, iface, src, name="GHAV")
    net = Network()
    bridge = Bridge(sched, iface, mapping, net)
    subs = world.make_subsystems(sched)
    bridge.attach_baseline(platform)
    for sub in subs.values():
        bridge.attach_unique(sub)
    bridge.start()
    platform.start()
    sched.run_until(2.0)
    cueing = subs["CueingProcessor"]
    assert cueing.unavailable == {"baro_alt_m": "deprecated element removed"}
    assert cueing.check() == ["baro_alt_m: unavailable (deprecated element removed)"]
    assert bridge.log.of_kind("bridge.unavailable")
    assert subs["PlanningDisplay"].check() == [] and subs["StatusLogger"].check() == []
    assert cueing.latest("pos_alt_m") is not None  # everything else still flows


def test_stall_and_resume_is_detected_annunciated_and_visible_to_consumers() -> None:
    s = world.build(1, seed=1)
    s.bridge.stall(at=2.2, duration=1.5)
    s.run(6.0)
    k = kinds(s)
    assert "bridge.stall" in k and "bridge.resume" in k
    assert "health.bridge_stall" in k and "health.discontinuity" in k
    voice = s.subsystems["VoiceRelay"]
    assert any(kind == "bridge_stall" for _, kind, _ in voice.annunciations)
    # Consumers see the gap in the source times of the data they received.
    times = [
        r.datum.source_time
        for r in s.subsystems["CueingProcessor"].received
        if r.native_name == "pos_alt_m"
    ]
    assert max(b - a for a, b in itertools.pairwise(times)) > 1.4
    # A slow message held through the stall is delivered late, and its age shows it.
    late = [r for r in voice.late if r.native_name == "time_utc_s"]
    assert late and max(r.age for r in late) > 0.5


def test_naive_delivery_stamping_hides_the_stall_age() -> None:
    s = world.build(1, seed=1, time_tag="delivery")
    s.bridge.stall(at=2.2, duration=1.5)
    s.run(6.0)
    assert not s.subsystems["VoiceRelay"].late  # looks fresh even though it was held


def test_stale_data_is_detected_and_recovery_is_logged() -> None:
    s = world.build(1, seed=1)
    s.run(1.0)
    s.platform.sources = {**s.platform.sources, "PowerStatus": lambda t: {}}  # type: ignore[misc]
    health = s.bridge.health
    health.observe(Message("NavigationState", {}, 0.5))  # earlier source time: out of order
    assert "health.out_of_order" in kinds(s)
    health.observe(Message("NotPublished", {}, 0.0))  # ignored
    s.sched.run_until(1.0)
    health._last_arrival["PowerStatus"] = -10.0
    health.scan()
    assert "health.stale" in kinds(s)
    health.scan()  # reported once only
    assert kinds(s).count("health.stale") == 1
    health.observe(Message("PowerStatus", {}, 1.0))
    assert "health.recovered" in kinds(s)


@pytest.fixture
def system() -> world.System:
    s = world.build(1, seed=1)
    s.run(1.0)
    return s


def test_containment_of_malformed_out_of_range_and_unauthorized(system: world.System) -> None:
    sub = system.subsystems["PlanningDisplay"]
    sub.send("show_page", {"page": "MAP", "priority": 1})  # legitimate
    sub.send("show_page", {"page": "MAP", "priority": 9})  # out of range
    sub.send("show_page", {"page": "NOPE", "priority": 1})  # bad enum
    sub.send("show_page", {"page": "MAP"})  # malformed
    sub.send("set_gear", {"state": "up"})  # unauthorized message type
    system.run(3.0)
    log = system.bridge.log
    assert len(log.of_kind("tm.reject_up")) == 3
    assert len(log.of_kind("cyber.block")) == 1 and system.bridge.cyber.blocked == 1
    assert [m.values for _, m in system.platform.accepted] == [{"page_id": 1, "priority": 1}]
    assert system.platform.rejected == []  # the baseline never saw any of it


def test_other_subsystems_cannot_send_what_they_do_not_declare(system: world.System) -> None:
    system.subsystems["CueingProcessor"].send("show_page", {"page": "MAP", "priority": 1})
    system.run(3.0)
    assert system.platform.accepted == []
    assert system.bridge.cyber.blocked == 1


def test_cyber_policy_can_be_restricted_and_blocks_downlinks(system: world.System) -> None:
    cyber = system.bridge.cyber
    cyber.allowed_down = frozenset(p for p in cyber.allowed_down if p[1] != "VoiceRelay")
    before = len(system.subsystems["VoiceRelay"].received)
    system.run(4.0)
    assert len(system.subsystems["VoiceRelay"].received) == before
    assert cyber.blocked > 0 and system.bridge.log.of_kind("cyber.block")


def test_uplink_during_a_stall_is_dropped_and_logged(system: world.System) -> None:
    system.bridge.stall(at=1.0, duration=1.0)
    system.run(1.2)
    system.subsystems["PlanningDisplay"].send("show_page", {"page": "MAP", "priority": 1})
    system.run(1.5)
    assert system.bridge.log.of_kind("bridge.drop_up")


def test_rate_cap_decimates_but_never_invents_samples(system: world.System) -> None:
    system.run(5.0)
    nav = [
        r for r in system.subsystems["PlanningDisplay"].received if r.native_name == "nav_lat_deg"
    ]
    gaps = [b.datum.source_time - a.datum.source_time for a, b in itertools.pairwise(nav)]
    assert min(gaps) >= 0.2 - 1e-6  # 5 Hz cap on a 20 Hz source
    cue = [
        r for r in system.subsystems["CueingProcessor"].received if r.native_name == "pos_lat_rad"
    ]
    gaps = [b.datum.source_time - a.datum.source_time for a, b in itertools.pairwise(cue)]
    assert max(gaps) == pytest.approx(0.05, abs=1e-6)  # 20 Hz source is not upsampled to 50 Hz
    assert system.bridge.uid.decimated > 0


def test_subsystem_checks_and_errors() -> None:
    sched = Scheduler()
    sub = UniqueSubsystem(sched, "X", "pubsub", world.make_subsystems(sched)["VoiceRelay"].needs)
    assert sub.check() == ["time_utc_s: no data", "power_state: no data"]
    with pytest.raises(RuntimeError, match="not connected"):
        sub.send("m", {})
    sub.on_data("time_utc_s", Datum("time_utc_s", 1.0, 0.0))
    sched.run_until(10.0)
    assert "time_utc_s: stale" in sub.check()
    sub.on_data("nope", Datum("nope", 1.0, 0.0))  # unknown input is recorded, not budgeted
    assert sub.need("nope") is None


def test_uid_rejects_unknown_protocols_and_inputs() -> None:
    s = world.build(1, seed=1)
    odd = UniqueSubsystem(s.sched, "Odd", "carrier_pigeon", ())
    with pytest.raises(ValueError, match="carrier_pigeon"):
        s.bridge.uid.connect(odd)
    with pytest.raises(KeyError):
        s.bridge.uid.deliver("PlanningDisplay", Datum("nope", 1.0, 0.0))


def test_platform_start_is_idempotent_and_deterministic() -> None:
    def trace(seed: int) -> list[float]:
        s = world.build(1, seed=seed)
        s.platform.start()
        s.run(2.0)
        return [r.datum.value for r in s.subsystems["StatusLogger"].received]

    assert trace(1) == trace(1)
