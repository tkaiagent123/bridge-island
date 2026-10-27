import random

import pytest
import world

from bridge_island.analysis.change_impact import (
    Change,
    Impact,
    diff_interfaces,
    impact_bridge,
    impact_p2p,
    monte_carlo,
    summarize,
)
from bridge_island.analysis.topology import (
    counts,
    counts_for_edges,
    edges_from_mapping,
    random_edges,
    sweep,
)
from bridge_island.codegen import load_module


def test_counts_follow_the_formulas() -> None:
    c = counts(n=10, m=4, edges=17)
    assert (c.p2p, c.p2p_worst_case, c.bridge, c.bridge_consolidated) == (17, 40, 14, 5)
    with pytest.raises(ValueError):
        counts(2, 2, 5)


def test_sweep_is_deterministic_and_bounded() -> None:
    a = sweep([5, 10], [2, 4], [0.2, 1.0], seed=3, repeats=2)
    assert a == sweep([5, 10], [2, 4], [0.2, 1.0], seed=3, repeats=2)
    assert len(a) == 16
    for r in a:
        assert r["p2p"] <= r["p2p_worst_case"] and r["bridge_consolidated"] <= r["bridge"]
        if r["density"] == 1.0:
            assert r["p2p"] == r["p2p_worst_case"]
    assert random_edges(random.Random(1), 3, 3, 0.0) == set()


def test_real_aviation_counts() -> None:
    mapping = load_module(world.HERE / "generated" / "mapping_v1.py")
    edges = edges_from_mapping(mapping)
    elements = [f"{m.name}.{e.name}" for m in world.interface(1).published for e in m.elements]
    c = counts_for_edges(elements, edges)
    assert (c.n, c.m, c.edges) == (len(elements), 4, 18)


def test_diff_finds_the_aviation_change_set() -> None:
    changes = diff_interfaces(world.interface(1), world.interface(2), world.RENAMES)
    assert {(c.kind, c.ref) for c in changes} == {
        ("rename", "NavigationState.groundspeed_kt"),
        ("unit", "NavigationState.altitude"),
        ("encoding", "NavigationState.heading"),
        ("rate", "AttitudeState"),
        ("add", "NavigationState.vertical_speed_mps"),
        ("remove", "AirData.baro_altitude_ft"),
    }
    assert diff_interfaces(world.interface(1), world.interface(1)) == []


def test_aviation_impact_bridge_touches_the_translator_and_the_removal_only() -> None:
    edges = edges_from_mapping(load_module(world.HERE / "generated" / "mapping_v1.py"))
    changes = diff_interfaces(world.interface(1), world.interface(2), world.RENAMES)
    # hand-counted from the v1 mapping: 6 edges read a renamed/unit/encoding/removed element
    assert impact_p2p(changes, edges) == Impact(3, 3, 9, 3)
    assert impact_bridge(changes, edges) == Impact(1, 2, 7, 2)


def test_additions_and_unused_elements_cost_nothing() -> None:
    edges = {("M.a", "S1")}
    assert impact_p2p([Change("add", "M.new")], edges) == Impact(0, 0, 0, 0)
    assert impact_bridge([Change("add", "M.new")], edges) == Impact(0, 0, 0, 1)
    assert impact_bridge([], edges) == Impact(0, 0, 0, 0)


def test_monte_carlo_is_reproducible_and_favours_the_bridge_on_average() -> None:
    rows = monte_carlo(seed=7, trials=300)
    assert rows == monte_carlo(seed=7, trials=300)
    p2p = summarize(rows, "p2p_subsystems_code_change")
    bridge = summarize(rows, "bridge_subsystems_code_change")
    assert bridge["mean"] < p2p["mean"]
    assert p2p["p5"] <= p2p["p50"] <= p2p["p95"]
    assert rows != monte_carlo(seed=8, trials=300)


def test_tests_failing_row_scales_with_the_arbitrary_assumption_but_the_gap_does_not() -> None:
    edges = edges_from_mapping(load_module(world.HERE / "generated" / "mapping_v1.py"))
    changes = diff_interfaces(world.interface(1), world.interface(2), world.RENAMES)
    rows = {
        k: (
            impact_p2p(changes, edges, k).tests_failing,
            impact_bridge(changes, edges, k).tests_failing,
        )
        for k in (0, 1, 2, 5)
    }
    assert rows == {0: (3, 1), 1: (9, 7), 2: (15, 13), 5: (33, 31)}  # as in docs/experiments.md
    assert {p - b for p, b in rows.values()} == {2}
