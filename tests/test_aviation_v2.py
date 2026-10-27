import itertools

import pytest
import world

from bridge_island.codegen import generate, load_module
from bridge_island.codegen.generate import spec_sha256


@pytest.fixture(scope="module")
def v2() -> world.System:
    system = world.build(2, seed=1)
    system.run(5.0)
    return system


@pytest.mark.parametrize("version", [1, 2])
def test_g5_committed_modules_match_a_fresh_build(version: int) -> None:
    spec = world.HERE / f"mapping_v{version}.yaml"
    generated = world.HERE / "generated" / f"mapping_v{version}.py"
    assert generated.read_text() == generate(spec, world.dictionary(version))
    assert generated.read_text().splitlines()[2] == f"# Spec SHA-256: {spec_sha256(spec)}"


def test_v2_removal_is_reported_to_the_affected_subsystem(v2: world.System) -> None:
    cueing = v2.subsystems["CueingProcessor"]
    assert cueing.unavailable == {"baro_alt_m": "element removed from baseline interface v2"}
    assert cueing.check() == [
        "baro_alt_m: unavailable (element removed from baseline interface v2)"
    ]
    assert v2.bridge is not None and v2.bridge.log.of_kind("bridge.unavailable")
    assert cueing.latest("pos_alt_m") is not None  # the rest of its inputs keep flowing


def test_v2_only_the_removal_reaches_a_unique_system(v2: world.System) -> None:
    for name in ("PlanningDisplay", "VoiceRelay", "StatusLogger"):
        assert v2.subsystems[name].check() == []
        assert not v2.subsystems[name].unavailable


def test_v2_passes_unchanged_data_through_and_converts_where_needed(v2: world.System) -> None:
    mapping = load_module(world.HERE / "generated" / "mapping_v2.py")
    identity = {(sub, native): ident for _, sub, native, ident in mapping.DOWNLINKS}
    assert (
        identity[("PlanningDisplay", "nav_alt_m")]
        and identity[("PlanningDisplay", "nav_heading_deg")]
    )
    assert not identity[("StatusLogger", "altitude_ft")]  # the logger still speaks feet
    nav = world.sources(2)["NavigationState"]
    rec = v2.subsystems["StatusLogger"].latest("altitude_ft")
    assert rec is not None
    assert rec.datum.value == pytest.approx(nav(rec.datum.source_time)["altitude"] / 0.3048)
    rec = v2.subsystems["PlanningDisplay"].latest("nav_speed_kmh")
    assert rec is not None
    assert rec.datum.value == pytest.approx(nav(rec.datum.source_time)["ground_speed_kt"] * 1.852)


def test_v2_faster_attitude_is_absorbed_by_the_uid(v2: world.System) -> None:
    times = [
        r.datum.source_time
        for r in v2.subsystems["CueingProcessor"].received
        if r.native_name == "roll_rad"
    ]
    gaps = [b - a for a, b in itertools.pairwise(times)]
    assert (
        min(gaps) >= 0.02 - 1e-6
    )  # capped at the subsystem's 50 Hz although the baseline sends 100 Hz
    emitted = v2.platform.emitted  # the baseline really did send 5x the navigation rate
    assert emitted["AttitudeState"] / emitted["NavigationState"] == pytest.approx(5.0, abs=0.2)


def test_v2_uplink_still_works_unchanged() -> None:
    system = world.build(2, seed=1)
    system.run(1.0)
    system.subsystems["PlanningDisplay"].send("show_page", {"page": "STATUS", "priority": 2})
    system.run(2.0)
    assert [m.values for _, m in system.platform.accepted] == [{"page_id": 2, "priority": 2}]
