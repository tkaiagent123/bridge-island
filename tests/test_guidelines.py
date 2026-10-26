"""One or more tests per design guideline, named after it."""

import re
from dataclasses import replace
from pathlib import Path

import pytest
import world

from bridge_island.baseline.platform import BaselinePlatform
from bridge_island.bridge import Bridge, InterfaceMismatch
from bridge_island.codegen import generate, load_module
from bridge_island.codegen.generate import spec_sha256
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Message
from bridge_island.core.links import BypassError, Network
from bridge_island.protocols.command_response import RemoteTerminal
from bridge_island.unique.subsystem import UniqueSubsystem

ROOT = Path(__file__).resolve().parent.parent
GENERATED = ROOT / "examples" / "aviation_invented" / "generated" / "mapping_v1.py"


@pytest.fixture(scope="module")
def run() -> world.System:
    system = world.build(1, seed=1)
    system.run(5.0)
    return system


# --- G1 complete retargeting -------------------------------------------------------


def test_g1_no_unique_system_has_a_direct_link_to_the_baseline(run: world.System) -> None:
    assert run.network.problems() == []
    assert run.network.bypass_links() == []
    peers = {(lk.a, lk.b) for lk in run.network.links}
    assert {sub for sub in run.subsystems} == {a for a, b in peers if b == "bridge"} - {"GHAV"}
    assert list(run.platform.bus.terminals.values()) == [run.bridge.oiu.rt]


def test_g1_a_bypass_cannot_be_constructed(run: world.System) -> None:
    sched = Scheduler()
    platform = BaselinePlatform(sched, world.interface(1), world.sources(1), name="GHAV")
    sub = world.make_subsystems(sched)["CueingProcessor"]
    with pytest.raises(BypassError):
        platform.connect(RemoteTerminal("sneaky"), owner=sub)
    assert platform.bus.terminals == {}


def test_g1_direct_wiring_is_detected() -> None:
    net = Network()
    baseline = BaselinePlatform(Scheduler(), world.interface(1), world.sources(1), name="GHAV")
    sub = UniqueSubsystem(Scheduler(), "Lonely", "pubsub", ())
    net.add(baseline)
    net.add(sub)
    assert net.problems() == ["Lonely has no link to the Bridge"]
    net.connect("GHAV", "Lonely", "command_response")
    assert "bypass link GHAV <-> Lonely" in net.problems()


# --- G2 translate only where needed ------------------------------------------------


def test_g2_identical_representations_pass_through_unchanged(run: world.System) -> None:
    mapping = load_module(GENERATED)
    for _, sub, _, identity in mapping.DOWNLINKS:
        if sub == "StatusLogger":
            assert identity
    logger = run.subsystems["StatusLogger"]
    rec = logger.latest("altitude_ft")
    assert rec is not None
    source = world.sources(1)["NavigationState"](rec.datum.source_time)
    assert rec.datum.value == source["altitude_ft"]  # bit-for-bit, no conversion
    assert run.bridge.tm.identity_count > 0 and run.bridge.tm.translated_count > 0


def test_g2_differing_representations_are_converted(run: world.System) -> None:
    rec = run.subsystems["PlanningDisplay"].latest("nav_alt_m")
    assert rec is not None
    feet = world.sources(1)["NavigationState"](rec.datum.source_time)["altitude_ft"]
    assert rec.datum.value == pytest.approx(feet * 0.3048)


def test_g2_codegen_refuses_an_unneeded_translation(spec_v1: Path, tmp_path: Path) -> None:
    from bridge_island.codegen import CodegenError

    path = tmp_path / "s.yaml"
    text = spec_v1.read_text()
    path.write_text(
        text.replace(
            "  - {from: PowerStatus.bus_voltage_v, to: StatusLogger.bus_voltage_v}",
            "  - {from: PowerStatus.bus_voltage_v, to: StatusLogger.bus_voltage_v,"
            " transforms: [{unit: [V, V]}]}",
        )
    )
    assert path.read_text() != text
    with pytest.raises(CodegenError, match="G2"):
        generate(path, world.dictionary(1))


# --- G3 time-tag integrity ---------------------------------------------------------


def test_g3_source_time_preserved(run: world.System) -> None:
    sub = run.subsystems["PlanningDisplay"]
    recs = [r for r in sub.received if r.native_name == "nav_alt_m"]
    assert recs
    for r in recs:
        # baseline validity time = emission time (k * 0.05) minus the 5 ms sample age
        k = (r.datum.source_time + 0.005) / 0.05
        assert k == pytest.approx(round(k), abs=1e-6)
        assert r.delivered_at > r.datum.source_time + 0.005  # Bridge and buses add latency
        assert r.age == pytest.approx(r.datum.age(r.delivered_at))


def test_g3_bridge_latency_is_visible_in_consumer_data_age() -> None:
    def mean_age(**opts: object) -> float:
        s = world.build(1, seed=1, **opts)
        s.run(3.0)
        ages = [
            r.age for r in s.subsystems["CueingProcessor"].received if r.native_name == "pos_alt_m"
        ]
        return sum(ages) / len(ages)

    fast = mean_age(processing_delay_s=0.001)
    slow = mean_age(processing_delay_s=0.050)
    assert slow - fast == pytest.approx(0.049, abs=1e-6)


def test_g3_delivery_time_stamping_hides_bridge_latency() -> None:
    def mean_age(time_tag: str) -> float:
        s = world.build(1, seed=1, time_tag=time_tag, processing_delay_s=0.050)
        s.run(3.0)
        ages = [
            r.age for r in s.subsystems["CueingProcessor"].received if r.native_name == "pos_alt_m"
        ]
        return sum(ages) / len(ages)

    honest, naive = mean_age("source"), mean_age("delivery")
    assert honest > 0.055  # sample age + Bridge delay + bus latency
    assert naive < 0.001  # only the last bus hop is visible: the data looks fresh


# --- G4 baseline immutability ------------------------------------------------------


def test_g4_oiu_rejects_what_the_baseline_does_not_publish() -> None:
    s = world.build(1, seed=1)
    oiu = s.bridge.oiu
    oiu.rt.on_receive[1](Message("Bogus", {"latitude": 1.0}, 0.0))
    oiu.rt.on_receive[1](Message("NavigationState", {"latitude": 1.0, "secret": 2}, 0.0))
    assert oiu.rejected_in == 2
    assert len(s.bridge.log.of_kind("oiu.reject_in")) == 2
    assert all(not sub.received for sub in s.subsystems.values())


def test_g4_oiu_emits_only_messages_the_baseline_accepts() -> None:
    s = world.build(1, seed=1)
    oiu = s.bridge.oiu
    assert not oiu.submit(Message("EngineCommand", {"throttle": 1}, 0.0))
    assert not oiu.submit(Message("DisplayRequest", {"page_id": 1}, 0.0))  # missing a field
    assert oiu.submit(Message("DisplayRequest", {"page_id": 1, "priority": 0}, 0.0))
    s.run(1.0)
    assert oiu.rejected_out == 2
    assert len(s.bridge.log.of_kind("oiu.reject_out")) == 2
    assert [m.spec_name for _, m in s.platform.accepted] == ["DisplayRequest"]
    assert s.platform.rejected == []  # nothing bad ever reached the baseline


def test_g4_baseline_rejects_and_logs_bad_input_on_its_own_side() -> None:
    p = BaselinePlatform(Scheduler(), world.interface(1), world.sources(1))
    p._accept(None)
    p._accept(Message("EngineCommand", {}, 0.0))
    p._accept(Message("DisplayRequest", {"page_id": 1}, 0.0))
    p._accept(Message("DisplayRequest", {"page_id": 99, "priority": 0}, 0.0))
    assert len(p.rejected) == 3 and p.accepted == []


def test_g4_bridge_refuses_a_mapping_that_does_not_fit_the_baseline() -> None:
    mapping = load_module(GENERATED)
    iface = world.interface(1)
    nav = iface.published_message("NavigationState")
    assert nav is not None
    trimmed = replace(nav, elements=tuple(e for e in nav.elements if e.name != "heading"))
    pub = tuple(trimmed if m.name == "NavigationState" else m for m in iface.published)
    with pytest.raises(InterfaceMismatch, match=r"NavigationState\.heading is not published"):
        Bridge(Scheduler(), replace(iface, published=pub), mapping)  # type: ignore[arg-type]
    with pytest.raises(InterfaceMismatch, match="baseline v1"):
        Bridge(Scheduler(), replace(iface, version=2), mapping)  # type: ignore[arg-type]
    with pytest.raises(InterfaceMismatch, match="not accepted"):
        Bridge(Scheduler(), replace(iface, accepted=()), mapping)  # type: ignore[arg-type]


# --- G5 compiled mapping -----------------------------------------------------------


def test_g5_generated_file_carries_header_and_spec_hash(spec_v1: Path) -> None:
    head = GENERATED.read_text().splitlines()[:3]
    assert head[0].startswith("# GENERATED FILE")
    assert "mapping_v1.yaml" in head[1]
    assert head[2] == f"# Spec SHA-256: {spec_sha256(spec_v1)}"


def test_g5_committed_module_matches_a_fresh_build(spec_v1: Path) -> None:
    assert GENERATED.read_text() == generate(spec_v1, world.dictionary(1))


def test_g5_no_runtime_tables_or_spec_reads(spec_v1: Path) -> None:
    source = GENERATED.read_text()
    assert "import yaml" not in source and "safe_load" not in source and "open(" not in source
    assert not re.search(r"^\w+\s*=\s*\{", source, re.M)  # no module-level dict tables
    mapping = load_module(GENERATED)
    assert spec_sha256(spec_v1) == mapping.SPEC_SHA256


def test_g5_editing_the_spec_changes_the_hash(spec_v1: Path, tmp_path: Path) -> None:
    edited = tmp_path / "mapping_v1.yaml"
    edited.write_text(spec_v1.read_text() + "\n# edited\n")
    assert spec_sha256(edited) != spec_sha256(spec_v1)


# --- G6 single-unit realization ----------------------------------------------------


def test_g6_one_bridge_object_composes_everything(run: world.System) -> None:
    b = run.bridge
    assert {"oiu", "tm", "uid", "health", "cyber"} == set(Bridge.PARTITIONS)
    for part in Bridge.PARTITIONS:
        assert getattr(b, part) is not None
    assert sum(1 for island in run.network.islands.values() if island == "bridge") == 1


def test_g6_components_are_separated_inside_the_bridge(run: world.System) -> None:
    b = run.bridge
    parts = [b.oiu, b.tm, b.uid, b.health, b.cyber]
    for part in parts:
        for value in vars(part).values():
            owner = getattr(value, "__self__", value)  # bound methods point at their owner
            assert not any(owner is other for other in parts if other is not part)
    assert len(set(Bridge.PARTITIONS.values())) >= 3  # baseline-facing, translation, ...
