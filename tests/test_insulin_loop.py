"""The insulin loop end to end: architectures, scenarios, metrics, G1-G8 (synthetic)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bridge_island.clinical import loop as L
from bridge_island.clinical import metrics as M
from bridge_island.clinical import scenarios as S
from bridge_island.clinical.guidelines import failsafe_problems, g7, g8, visibility_problems
from bridge_island.examples import load_world
from bridge_island.rules import check_rules
from bridge_island.scenario import ScenarioError

VP = L.patients(1)


def run(arch: str, name: str, patient: int = 0) -> L.Loop:
    loop = L.build_loop(arch, VP[patient], S.by_name(name))
    loop.run()
    return loop


@pytest.fixture(scope="module")
def nominal() -> dict[str, dict]:
    return {a: M.measure(run(a, "nominal")) for a in L.ARCHS}


def test_all_three_architectures_perform_alike_on_a_nominal_day(nominal: dict) -> None:
    keys = ("tir_70_180_min", "below_70_min", "above_180_min", "unsafe_age_min")
    assert len({tuple(r[k] for k in keys) for r in nominal.values()}) == 1
    for r in nominal.values():
        assert r["detected"] is None and r["commands_unconfirmed"] == 0
        assert r["failsafe_unneeded_min"] == 0.0 and r["unit_errors_to_controller"] == 0


def test_the_bridge_adds_latency_on_the_command_path(nominal: dict) -> None:
    assert (
        nominal["direct"]["host_to_pump_median_s"]
        < nominal["bridge-basic"]["host_to_pump_median_s"]
    )
    assert nominal["bridge-contract"]["host_to_pump_peak_s"] <= 60.0


def test_runs_are_repeatable_for_a_seed() -> None:
    a, b = (M.measure(run("bridge-contract", "stale_burst", 4)) for _ in range(2))
    assert a == b
    one = M.critical_events(run("bridge-contract", "pump_silent", 4))
    assert one == M.critical_events(run("bridge-contract", "pump_silent", 4))


def test_a_stale_adapter_misdoses_a_new_pump_silently() -> None:
    direct = M.measure(run("direct", "pump_fw_v2", 3))
    bridge = M.measure(run("bridge-basic", "pump_fw_v2", 3))
    assert direct["unit_errors_to_pump"] > 100 and direct["delivered_minus_commanded_u"] < -10
    assert bridge["unit_errors_to_pump"] == 0 and abs(bridge["delivered_minus_commanded_u"]) < 0.5


def test_the_contract_flags_stale_glucose_that_direct_acts_on() -> None:
    direct = M.measure(run("direct", "stale_burst"))
    contract = M.measure(run("bridge-contract", "stale_burst"))
    assert direct["unsafe_age_min"] > 0 and contract["unsafe_age_min"] == 0
    assert contract["detected"] and contract["detected_by"] == "contract"


def test_a_replay_doubles_the_bolus_unless_the_contract_blocks_it() -> None:
    rows = {a: M.measure(run(a, "replayed_command")) for a in L.ARCHS}
    assert rows["direct"]["delivered_minus_commanded_u"] > 1
    assert rows["bridge-basic"]["delivered_minus_commanded_u"] > 1
    assert rows["bridge-contract"]["delivered_minus_commanded_u"] < 0.5
    assert rows["bridge-contract"]["time_to_detect_s"] == 0.0


def test_a_bridge_stall_fails_every_path_and_direct_has_nothing_to_stall() -> None:
    rows = {a: M.measure(run(a, "bridge_stall")) for a in L.ARCHS}
    assert rows["direct"]["paths_failed_in_stall"] == 0
    assert rows["bridge-basic"]["paths_failed_in_stall"] == 3
    assert rows["bridge-contract"]["paths_failed_in_stall"] == 3
    assert rows["bridge-contract"]["unsafe_age_min"] > 0  # the contract stalls with the Bridge
    assert rows["bridge-contract"]["detected_by"] in ("contract", "display")


def test_the_pump_reverts_to_programmed_basal_when_commands_expire() -> None:
    loop = run("bridge-basic", "bridge_stall")
    pump = loop.pump("PumpA")
    stall = next(e.time for e in loop.bridge.log.events if e.kind == "bridge.stall")  # type: ignore[union-attr]
    assert not any(stall + 60 < t < stall + 1200 for t, *_ in pump.executed)


def test_critical_events_cover_the_required_categories() -> None:
    rows = M.critical_events(run("bridge-contract", "pump_silent"))
    cats = {r["category"] for r in rows}
    assert {"command", "confirmation", "malfunction", "alarm", "acknowledgment"} <= cats
    rows = M.critical_events(run("bridge-contract", "bridge_stall"))
    assert any(r["category"] == "connectivity" and "bridge.stall" in r["detail"] for r in rows)


def test_alarms_reach_the_display_late_when_it_is_throttled() -> None:
    r = M.measure(run("bridge-contract", "display_throttle"))
    assert r["alarms_raised"] > 0 and r["alarms_late"] + r["alarms_lost"] > 0
    assert r["below_70_min"] > 0  # the scenario produces the low it is about


# ---- scenario files ------------------------------------------------------------------------


def test_the_ten_scenarios_are_shipped_and_parse() -> None:
    names = [p.stem for p in S.shipped()]
    assert len(names) == 10 and "nominal" in names and "bridge_stall" in names
    for path in S.shipped():
        scenario, patients, archs = S.load(path)
        assert patients == "all" and archs == L.ARCHS and scenario.duration_s == 86400.0


@pytest.mark.parametrize(
    "text, match",
    [
        ("[]", "mapping"),
        ("example: medical", "insulin-loop"),
        ("example: insulin-loop\nduration_h: 49", "duration_h"),
        ("example: insulin-loop\nseed: x", "seed"),
        ("example: insulin-loop\npatients: VP31", "patients"),
        ("example: insulin-loop\narchitectures: [p2p]", "architectures"),
        ("example: insulin-loop\nfaults: {}", "faults"),
        ("example: insulin-loop\nfaults: [{kind: x, at: '01:00'}]", "kind"),
        ("example: insulin-loop\nfaults: [{kind: bridge_stall}]", "at"),
        ("example: insulin-loop\nfaults: [{kind: bridge_stall, at: noon}]", "at"),
        (
            "example: insulin-loop\nfaults: [{kind: bridge_stall, at: '01:00', minutes: -1}]",
            "minutes",
        ),
        ("example: insulin-loop\nfaults: [{kind: bridge_stall, at: '01:00', bogus: 1}]", "unknown"),
        (
            "example: insulin-loop\nfaults: [{kind: display_throttle, at: '01:00', drop: -1}]",
            "drop",
        ),
        ("example: insulin-loop\nfaults: [{kind: meal_eaten, at: lunch, meal: brunch}]", "meal"),
        (
            "example: insulin-loop\nfaults: [{kind: sensor_swap, at: '08:00'},"
            " {kind: pump_swap, at: '08:00'}]",
            "one device swap",
        ),
        ("example: insulin-loop\nwho: me", "unknown key"),
    ],
)
def test_bad_scenarios_are_refused(text: str, match: str, tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ScenarioError, match=match):
        S.load(path)


def test_an_unknown_scenario_name_is_refused() -> None:
    with pytest.raises(ScenarioError, match="choose from"):
        S.by_name("nope")


def test_fault_times_resolve_against_each_patients_meals() -> None:
    meals = L.meal_plan(VP[0], 1)
    lunch = next(m for m in meals if m.name == "lunch")
    assert L.Fault("x", "lunch+15").resolve(meals) == lunch.time + 900
    assert L.Fault("x", "lunch-30").resolve(meals) == lunch.time - 1800
    assert L.Fault("x", "08:00").resolve(meals) == 28800
    with pytest.raises(ValueError):
        L.Fault("x", "teatime").resolve(meals)


def test_unknown_architecture_and_versions_are_refused() -> None:
    with pytest.raises(ValueError, match="architecture"):
        L.build_loop("p2p", VP[0])
    with pytest.raises(ValueError, match="baseline v1"):
        L.build_loop("bridge-basic", VP[0], S.by_name("sensor_swap_units"), version=2)
    with pytest.raises(ValueError, match="running baseline"):
        L.build_default_direct(1, 1, adapters_from=2)


def test_the_loop_runs_on_baseline_v2_through_the_v2_mapping() -> None:
    loop = L.build_loop("bridge-basic", VP[0], L.NOMINAL, version=2)
    loop.run()
    r = M.measure(loop)
    assert r["below_54_min"] == 0 and r["commands_unconfirmed"] == 0
    assert "loop_mode" in loop.display.unavailable


# ---- the guidelines ------------------------------------------------------------------------


WORLD = load_world("insulin-loop")


@pytest.mark.parametrize("version", [1, 2])
def test_g1_to_g6_hold_for_the_insulin_loop(version: int) -> None:
    results = {r.rule: r for r in check_rules(WORLD, version)}
    assert list(results) == ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"]
    assert all(r.passed for r in results.values()), [r for r in results.values() if not r.passed]
    assert "contract-monitor" in results["G6"].detail


def test_g7_every_loss_state_ends_in_failsafe() -> None:
    ok, detail = g7(WORLD, 1)
    assert ok and "4 of 4" in detail


def test_g7_a_bridge_without_the_contract_has_no_failsafe() -> None:
    loop = L.build_loop("bridge-contract", VP[0], L.LoopScenario("ok", duration_s=7200.0))
    loop.run()
    assert failsafe_problems(loop) == ["the contract never entered fail-safe"]


def test_g8_every_alarm_is_seen_or_logged_undelivered() -> None:
    ok, detail = g8(WORLD, 1)
    assert ok and "degraded states" in detail


def test_g8_an_alarm_nobody_handled_is_reported() -> None:
    loop = L.build_loop("bridge-contract", VP[0], L.LoopScenario("ok", duration_s=600.0))
    loop.run()
    contract = loop.bridge.contract  # type: ignore[union-attr]
    contract.raise_alarm("test", "never acknowledged")  # type: ignore[union-attr]
    assert visibility_problems(loop)


def test_g7_and_g8_are_not_claimed_for_baseline_v2() -> None:
    assert g7(WORLD, 2)[0] and "outside" in g7(WORLD, 2)[1]
    assert g8(WORLD, 2)[0] and "outside" in g8(WORLD, 2)[1]


def test_other_examples_keep_their_six_rules() -> None:
    assert [r.rule for r in check_rules(load_world("medical"))] == [
        "G1",
        "G2",
        "G3",
        "G4",
        "G5",
        "G6",
    ]
