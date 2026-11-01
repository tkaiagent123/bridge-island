"""The numbers quoted in docs/experiments.md must match the seeded runs."""

from bridge_island.analysis import change_impact as ci
from bridge_island.analysis import topology


def test_monte_carlo_numbers_in_the_docs() -> None:
    rows = ci.monte_carlo(1, 1000)

    def cell(key: str) -> str:
        s = ci.summarize(rows, key)
        return f"{s['mean']:.1f} [{s['p5']:.0f}, {s['p95']:.0f}]"

    assert cell("p2p_subsystems_code_change") == "6.0 [0, 15]"
    assert cell("bridge_subsystems_code_change") == "2.0 [0, 9]"
    assert cell("p2p_modules_touched") == "6.0 [0, 15]"
    assert cell("bridge_modules_touched") == "2.9 [0, 10]"
    assert cell("p2p_tests_failing") == "14.9 [0, 42]"
    assert cell("bridge_tests_failing") == "10.9 [0, 35]"
    assert cell("p2p_components_reverified") == "7.8 [1, 17]"
    assert cell("bridge_components_reverified") == "3.0 [1, 10]"


def test_topology_numbers_in_the_docs() -> None:
    rows = topology.sweep([5, 10, 20, 40, 80], [2, 4, 8, 16], [0.1, 0.3, 0.6], 1, repeats=5)
    picked = {
        m: [r for r in rows if r["n"] == 40 and r["density"] == 0.3 and r["m"] == m]
        for m in (2, 4, 8, 16)
    }
    means = {m: round(sum(r["p2p"] for r in rs) / len(rs), 1) for m, rs in picked.items()}
    assert means == {2: 25.2, 4: 47.4, 8: 93.2, 16: 187.8}
    assert [picked[m][0]["bridge"] for m in picked] == [42, 44, 48, 56]
    assert [picked[m][0]["bridge_consolidated"] for m in picked] == [3, 5, 9, 17]


def test_age_measure_numbers_in_the_docs() -> None:
    import world

    from bridge_island.summary import summarize

    expected = {
        None: {
            "PlanningDisplay": (8.5, 108.5, 208.5),
            "CueingProcessor": (6.7, 28.8, 106.7),
            "VoiceRelay": (6.7, 506.6, 1006.7),
            "StatusLogger": (1005.5, 1505.5, 2005.5),
        },
        (2.2, 1.5): {
            "PlanningDisplay": (10.0, 267.8, 1708.0),
            "CueingProcessor": (6.9, 173.6, 1606.2),
            "VoiceRelay": (94.1, 594.0, 1706.2),
            "StatusLogger": (912.6, 1412.6, 2005.5),
        },
    }
    for stall, rows in expected.items():
        system = world.build(1, 1)
        if stall:
            assert system.bridge is not None
            system.bridge.stall(*stall)
        system.run(8.0)
        summary = summarize(system)["subsystems"]
        for name, (arrival, average, peak) in rows.items():
            s = summary[name]
            got = (
                round(s["mean_age_ms"], 1),
                round(s["time_avg_age_ms"], 1),
                round(s["peak_age_ms"], 1),
            )
            assert got == (arrival, average, peak), (stall, name, got)
        if stall:
            assert round(summary["CueingProcessor"]["stale_s"], 2) == 1.36
