import math

import pytest
import world

from bridge_island.baseline.platform import BaselinePlatform
from bridge_island.bridge import Bridge, InterfaceMismatch
from bridge_island.codegen import load_module
from bridge_island.core.clock import Scheduler
from bridge_island.core.links import BypassError
from bridge_island.p2p.direct import attach_direct


def values(system: world.System, sub: str, native: str) -> dict[float, float]:
    return {
        r.datum.source_time: r.datum.value
        for r in system.subsystems[sub].received
        if r.native_name == native
    }


def test_both_architectures_deliver_correct_values() -> None:
    """Each delivered value matches the ground-truth signal at its source time."""
    nav, att = world.sources(1)["NavigationState"], world.sources(1)["AttitudeState"]
    truth = {
        ("PlanningDisplay", "nav_alt_m"): lambda t: nav(t)["altitude"] * 0.3048,
        ("CueingProcessor", "roll_rad"): lambda t: math.radians(att(t)["roll_deg"]),
        ("StatusLogger", "altitude_ft"): lambda t: nav(t)["altitude"],
    }
    for system in (world.build(1), world.build_p2p(1)):
        system.run(4.0)
        for (sub, native), expected in truth.items():
            got = values(system, sub, native)
            assert len(got) >= 3
            assert all(v == pytest.approx(expected(t)) for t, v in got.items())


def test_p2p_has_the_bypass_links_that_the_bridge_forbids() -> None:
    p2p = world.build_p2p(1)
    assert len(p2p.network.bypass_links()) == 4
    assert p2p.network.problems()
    assert world.build(1).network.problems() == []


def test_a_platform_that_enforces_the_bridge_refuses_direct_adapters() -> None:
    sched = Scheduler()
    platform = BaselinePlatform(sched, world.interface(1), world.sources(1))
    sub = world.make_subsystems(sched)["VoiceRelay"]
    with pytest.raises(BypassError):
        attach_direct(
            platform,
            sub,
            load_module(world.HERE / "generated" / "mapping_v1.py"),
            world.interface(1),
        )


def test_p2p_adapters_written_for_v1_break_silently_on_v2() -> None:
    p2p = world.build_p2p(2, adapters_from=1)
    p2p.run(4.0)
    truth_m = world.sources(2)["NavigationState"]
    rec = p2p.subsystems["PlanningDisplay"].latest("nav_alt_m")
    assert rec is not None
    wrong = rec.datum.value
    right = truth_m(rec.datum.source_time)["altitude"]
    assert wrong == pytest.approx(right * 0.3048) and wrong != pytest.approx(
        right
    )  # converted twice
    assert p2p.adapters is not None
    assert p2p.adapters["PlanningDisplay"].errors  # the renamed field is a lookup error
    # Nothing tells the CueingProcessor the barometric altitude is gone: it just stops.
    assert p2p.subsystems["CueingProcessor"].check()[-1] == "baro_alt_m: no data"


def test_the_bridge_refuses_an_old_mapping_loudly_instead() -> None:
    old = world.HERE / "generated" / "mapping_v1.py"
    with pytest.raises(InterfaceMismatch):
        Bridge(Scheduler(), world.interface(2), load_module(old))  # type: ignore[arg-type]


def test_p2p_with_updated_adapters_works_on_v2() -> None:
    p2p = world.build_p2p(2)
    p2p.run(4.0)
    assert p2p.adapters is not None and not any(a.errors for a in p2p.adapters.values())
    assert p2p.subsystems["CueingProcessor"].unavailable  # the code change the removal forces


def test_p2p_has_no_containment_of_a_faulty_subsystem() -> None:
    p2p = world.build_p2p(1)
    p2p.run(1.0)
    assert p2p.adapters is not None
    p2p.subsystems["PlanningDisplay"].send("show_page", {"page": "MAP", "priority": 1})
    p2p.subsystems["PlanningDisplay"].send(
        "show_page", {"page": "MAP", "priority": 9}
    )  # own code catches it
    p2p.adapters["CueingProcessor"].send_raw(
        "DisplayRequest", {"page_id": 99, "priority": 0}
    )  # faulty software
    p2p.run(3.0)
    assert [m.values["page_id"] for _, m in p2p.platform.accepted] == [1]
    assert len(p2p.platform.rejected) == 1  # the baseline had to deal with it
    assert len(p2p.adapters["PlanningDisplay"].errors) == 1
