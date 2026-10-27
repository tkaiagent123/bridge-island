"""Runnable checks of the design guidelines G1 to G6 against any example world.

Used by ``bridge-island check-rules`` and by the test suite.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType

from bridge_island.baseline.platform import BaselinePlatform
from bridge_island.bridge import Bridge
from bridge_island.codegen import generate, load_module
from bridge_island.codegen.generate import spec_sha256
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Message
from bridge_island.core.links import BypassError
from bridge_island.examples import generated_path, spec_path
from bridge_island.protocols.command_response import RemoteTerminal


@dataclass(frozen=True)
class RuleResult:
    rule: str
    title: str
    passed: bool
    detail: str


def _g1(world: ModuleType, version: int) -> tuple[bool, str]:
    system = world.build(version, 1)
    system.run(1.0)
    problems = system.network.problems()
    sched = Scheduler()
    platform = BaselinePlatform(sched, world.interface(version), world.sources(version))
    sub = next(iter(world.make_subsystems(sched).values()))
    try:
        platform.connect(RemoteTerminal("bypass"), owner=sub)
        refused = False
    except BypassError:
        refused = True
    ok = not problems and refused and len(system.platform.bus.terminals) == 1
    return (
        ok,
        f"{len(system.network.links)} links, bypass links: {len(system.network.bypass_links())}, direct attach refused: {refused}",
    )


def _g2(world: ModuleType, version: int) -> tuple[bool, str]:
    system = world.build(version, 1)
    system.run(4.0)
    mapping = load_module(generated_path(world, version))
    dic = world.dictionary(version)
    needs = {(s, n.native_name): n for s, ns in dic.needs.items() for n in ns}
    sources = world.sources(version)
    checked = 0
    for ref, sub, native, identity in mapping.DOWNLINKS:
        element = dic.interface.element(ref)
        assert element is not None
        same = element.representation.same_value_form(needs[(sub, native)].representation)
        if same != identity:
            return (
                False,
                f"{ref} -> {sub}.{native}: identity flag {identity} but forms match is {same}",
            )
        rec = system.subsystems[sub].latest(native)
        if identity and rec is not None:
            message, _, field = ref.partition(".")
            if rec.datum.value != sources[message](rec.datum.source_time)[field]:
                return False, f"{ref} -> {sub}.{native}: value changed on an identity path"
            checked += 1
    n_id = sum(1 for d in mapping.DOWNLINKS if d[3])
    return (
        n_id > 0 and checked > 0,
        f"{n_id} of {len(mapping.DOWNLINKS)} paths are identity; {checked} verified bit for bit",
    )


def _mean_age(world: ModuleType, version: int, mode: str) -> float:
    mapping = load_module(generated_path(world, version))
    _, sub, native, _ = mapping.DOWNLINKS[0]
    system = world.build(version, 1, time_tag=mode, processing_delay_s=0.050)
    system.run(3.0)
    ages = [r.age for r in system.subsystems[sub].received if r.native_name == native]
    return float(sum(ages) / len(ages))


def _g3(world: ModuleType, version: int) -> tuple[bool, str]:
    honest, naive = _mean_age(world, version, "source"), _mean_age(world, version, "delivery")
    ok = honest > 0.050 and naive < honest - 0.045
    return (
        ok,
        f"mean data age with a 50 ms Bridge delay: source time {honest * 1000:.1f} ms, delivery-time stamping {naive * 1000:.1f} ms (hides the delay)",
    )


def _g4(world: ModuleType, version: int) -> tuple[bool, str]:
    system = world.build(version, 1)
    assert system.bridge is not None
    oiu = system.bridge.oiu
    spec = world.interface(version).published[0]
    oiu.rt.on_receive[spec.subaddress](Message("Bogus", {"x": 1}, 0.0))
    refused_out = not oiu.submit(Message("Unspecified", {"x": 1}, 0.0))
    system.run(1.0)
    ok = oiu.rejected_in == 1 and refused_out and system.platform.rejected == []
    return (
        ok,
        f"unpublished input rejected: {oiu.rejected_in == 1}, unspecified output refused: {refused_out}, baseline rejections: {len(system.platform.rejected)}",
    )


def _g5(world: ModuleType, version: int) -> tuple[bool, str]:
    spec, gen = spec_path(world, version), generated_path(world, version)
    text = gen.read_text(encoding="utf-8")
    lines = text.splitlines()
    header = (
        lines[0].startswith("# GENERATED FILE")
        and lines[2] == f"# Spec SHA-256: {spec_sha256(spec)}"
    )
    fresh = text == generate(spec, world.dictionary(version))
    clean = "import yaml" not in text and "safe_load" not in text
    return (
        header and fresh and clean,
        f"header and hash match: {header}, equals a fresh build: {fresh}, no spec reads at run time: {clean}",
    )


def _g6(world: ModuleType, version: int) -> tuple[bool, str]:
    system = world.build(version, 1)
    b = system.bridge
    assert b is not None
    parts = [b.oiu, b.tm, b.uid, b.health, b.cyber]
    one = sum(1 for i in system.network.islands.values() if i == "bridge") == 1
    separate = True
    for part in parts:
        for value in vars(part).values():
            owner = getattr(value, "__self__", value)
            separate &= not any(owner is other for other in parts if other is not part)
    return (
        one and separate and len(set(Bridge.PARTITIONS.values())) >= 3,
        f"one Bridge object: {one}, components hold no direct references to each other: {separate}, partitions: {sorted(set(Bridge.PARTITIONS.values()))}",
    )


RULES: list[tuple[str, str, Callable[[ModuleType, int], tuple[bool, str]]]] = [
    ("G1", "Complete retargeting", _g1),
    ("G2", "Translate only where needed", _g2),
    ("G3", "Time-tag integrity", _g3),
    ("G4", "Baseline immutability", _g4),
    ("G5", "Compiled mapping", _g5),
    ("G6", "Single-unit realization", _g6),
]


def check_rules(world: ModuleType, version: int | None = None) -> list[RuleResult]:
    version = version or world.VERSIONS[0]
    return [RuleResult(r, title, *fn(world, version)) for r, title, fn in RULES]
