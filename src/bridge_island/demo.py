"""`bridge-island demo`: run an example on the Bridge, then show what a baseline change costs."""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType

from bridge_island.analysis.change_impact import diff_interfaces, impact_bridge, impact_p2p
from bridge_island.analysis.topology import edges_from_mapping
from bridge_island.codegen import load_module
from bridge_island.examples import generated_path

SYNTHETIC = "all content is invented; numbers are synthetic, illustrative"


def run_demo(
    world: ModuleType,
    seed: int = 1,
    out: Callable[[str], None] = print,
    rate_policy: str = "cap",
) -> None:
    out(f"bridge-island demo: {world.NAME} ({SYNTHETIC}); rate policy: {rate_policy}")
    for version in world.VERSIONS:
        system = world.build(version, seed, rate_policy=rate_policy)
        system.run(6.0)
        bridge = system.bridge
        assert bridge is not None
        out(f"\n== baseline interface v{version}: 6 s simulated, seed {seed} ==")
        out(
            f"{'subsystem':<17}{'native bus':<18}{'received':>9}{'mean age':>10}{'late':>6}  issues"
        )
        for name, sub in system.subsystems.items():
            ages = [r.age for r in sub.received]
            mean = f"{1000 * sum(ages) / len(ages):.1f} ms" if ages else "-"
            issues = "; ".join(sub.check()) or "none"
            out(
                f"{name:<17}{sub.protocol:<18}{len(sub.received):>9}{mean:>10}{len(sub.late):>6}  {issues}"
            )
        out(
            f"Bridge: {bridge.tm.identity_count} identity pass-throughs, "
            f"{bridge.tm.translated_count} translated, {bridge.uid.decimated} decimated to fit "
            f"subsystem rates, {bridge.uid.estimates} estimated samples delivered, "
            f"{bridge.cyber.blocked} blocked by the cyber monitor"
        )
        out(f"G1 links to the baseline that skip the Bridge: {len(system.network.bypass_links())}")
        for event in bridge.log.of_kind("bridge.unavailable"):
            out(f"REPORTED to the subsystem, not silent: {event.detail}")

    first, last = world.VERSIONS[0], world.VERSIONS[-1]
    changes = diff_interfaces(world.interface(first), world.interface(last), world.RENAMES)
    edges = edges_from_mapping(load_module(generated_path(world, first)))
    out(f"\n== baseline change v{first} -> v{last}: {len(changes)} changes ==")
    for c in changes:
        out(f"  {c.kind:<9}{c.ref}")
    out(f"{'':<34}{'P2P':>6}{'Bridge':>8}")
    p, b = impact_p2p(changes, edges), impact_bridge(changes, edges)
    for label, key in [
        ("unique subsystems needing code change", "subsystems_code_change"),
        ("modules touched", "modules_touched"),
        ("tests failing before the fix", "tests_failing"),
        ("components needing re-verification", "components_reverified"),
    ]:
        out(f"  {label:<32}{getattr(p, key):>6}{getattr(b, key):>8}")
    out("The Bridge concentrates the change in one mapping; it does not make it disappear.")
