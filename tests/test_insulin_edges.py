"""Edge cases from mutation testing: boundaries, defaults, and checks that must catch a fault."""

from __future__ import annotations

import copy
import math
from typing import Any

import pytest

from bridge_island.bridge.contract import ContractMonitor, load_contract, parse_contract
from bridge_island.clinical import envelope as Env
from bridge_island.clinical import guidelines as G
from bridge_island.clinical import loop as L
from bridge_island.clinical import metrics as M
from bridge_island.clinical import scenarios as S
from bridge_island.clinical.controller import Controller, Settings
from bridge_island.clinical.devices import Display, Pump
from bridge_island.clinical.physiology import Patient, Therapy
from bridge_island.core.clock import Scheduler
from bridge_island.core.elements import Datum, Message
from bridge_island.core.events import EventLog
from bridge_island.examples import examples_dir
from bridge_island.yamlutil import load_yaml

PATH = examples_dir() / "insulin_loop_invented" / "contract.yaml"
RAW = load_yaml(PATH.read_text(encoding="utf-8"))
SPEC = load_contract(PATH).with_failsafe_basal(0.8)
VP = L.patients(1)


# ---- the contract ------------------------------------------------------------------------------


def test_a_boolean_is_not_a_number_for_a_data_item() -> None:
    item = SPEC.role("glucose_status")  # range 0-4: True would pass as 1 if it counted as a number
    assert item is not None and item.violation(True) is not None and item.violation(1) is None


@pytest.mark.parametrize(
    "path, value",
    [
        (("timing", "check_period_s"), 0.5),
        (("timing", "latency_budget_s"), 0),
        (("data_items", 0, "range"), [100, 100]),
    ],
)
def test_contract_boundary_values_are_accepted(path: tuple[Any, ...], value: Any) -> None:
    data = copy.deepcopy(RAW)
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    parse_contract(data)


def _monitor() -> tuple[Scheduler, EventLog, list[Message], ContractMonitor]:
    sched, log, up = Scheduler(), EventLog(), []
    mon = ContractMonitor(SPEC, sched, log, up.append, lambda m: None, lambda *a: None)
    return sched, log, up, mon


def test_ages_in_log_messages_are_measured_from_when_the_alarm_was_raised() -> None:
    sched, log, _, mon = _monitor()
    sched.run_until(100.0)
    alarm = mon.raise_alarm("test", "x")
    sched.run_until(150.0)
    mon.check_up("CompanionDisplay", Message("AlarmAck", {"alarm_id": alarm.id}, 150.0))
    assert "after 50 s" in log.of_kind("contract.ack")[0].detail
    sched, log, _, mon = _monitor()
    sched.run_until(100.0)
    mon.raise_alarm("test", "y")
    for t in (230.0, 350.0, 470.0):
        sched.run_until(t)
        mon.scan()
    assert "age 370 s" in log.of_kind("contract.alarm_undelivered")[0].detail


def test_glucose_is_flagged_only_once_older_than_the_flag_age() -> None:
    sched, _log, up, mon = _monitor()
    mon.handover(
        Message("GlucoseReading", {"glucose": 120.0, "sensor_status": 0, "source_time": 0.0}, 0.0)
    )
    sched.run_until(570.0)  # exactly the flag age: not yet
    mon.scan()
    assert not up
    sched.run_until(571.0)
    mon.scan()
    assert up and up[0].values["sensor_status"] == SPEC.failsafe_status


def test_an_unconfirmed_report_says_nothing_was_delivered() -> None:
    sched, _, up, mon = _monitor()
    mon.check_down(
        Message(
            "DoseCommand", {"command_id": 4, "basal": 1.0, "bolus": 2.0, "valid_until": 720.0}, 0.0
        )
    )
    for t in (130.0, 260.0, 390.0, 520.0):
        sched.run_until(t)
        mon.scan()
    told = [m for m in up if m.spec_name == "DeliveryConfirmation"]
    assert told and told[0].values["delivered"] == 0.0


# ---- the controller ----------------------------------------------------------------------------

TH = Therapy(1.0, 40.0, 10.0)


def _controller(
    version: int = 1, glucose: float = 200.0, status: int = 0, source_time: float = 0.0
) -> Controller:
    c = Controller(TH, [], version)
    values = {"glucose": glucose, "sensor_status": status, "source_time": source_time}
    msg = Message("GlucoseReading", values, 0.0)
    c.attach(lambda: [(0.0, msg)])
    return c


def test_a_high_reading_is_used_and_a_failsafe_one_is_not() -> None:
    assert _controller(glucose=400.0, status=2).command(60.0)["basal"] > TH.basal_u_h
    assert _controller(glucose=40.0, status=1).command(60.0)["basal"] == 0.0
    assert _controller(glucose=400.0, status=4).command(60.0)["basal"] == TH.basal_u_h


def test_no_correction_once_insulin_on_board_reaches_the_limit() -> None:
    c = _controller(glucose=400.0)  # wants more than the limit, so only the limit stops it
    c._iob, c._iob_time = Settings().iob_limit_factor * TH.basal_u_h, 60.0
    assert c.command(60.0)["basal"] == TH.basal_u_h


def test_baseline_v2_sends_basal_in_milliunits_per_minute_and_a_trend() -> None:
    c = _controller(2, glucose=120.0)
    assert c.command(60.0)["basal"] == pytest.approx(TH.basal_u_h / 0.06)
    first = c.status(60.0)
    assert first["trend"] == 0.0 and "loop_mode" not in first and first["insulin_on_board"] == 0.0
    rising = Message(
        "GlucoseReading", {"glucose": 150.0, "sensor_status": 0, "source_time": 300.0}, 300.0
    )
    c.attach(lambda: [(0.0, rising)])
    c._seen = 0
    assert c.status(360.0)["trend"] == pytest.approx((150.0 - 120.0) / 5.0)
    assert _controller(2, status=4).status(60.0)["trend"] == 0.0


def test_the_pump_alarm_starts_after_exactly_the_timeout() -> None:
    s = Settings()
    c = _controller(glucose=120.0, source_time=s.pump_timeout_s)
    c.command(s.pump_timeout_s)
    assert c.alarm == "NONE"
    c.command(s.pump_timeout_s + 300.0)
    assert c.alarm == "PUMP_COMM"
    t = 3 * s.pump_timeout_s  # after the start-up quiet period
    c = _controller(glucose=120.0, source_time=t + s.pump_timeout_s)
    c.unconfirmed_at = c.last_confirm_at = t
    c.command(t + s.pump_timeout_s - 300.0)
    assert c.alarm == "PUMP_COMM"  # told unconfirmed: alarmed for the timeout
    c.last_confirm_at = t + s.pump_timeout_s
    c.command(t + s.pump_timeout_s)
    assert c.alarm == "NONE"


# ---- devices -----------------------------------------------------------------------------------


def _pump(firmware: str = "A") -> tuple[Scheduler, Pump, list[Message]]:
    sched = Scheduler()
    w = L.world()
    name = f"Pump{firmware}"
    pump = Pump(sched, name, firmware, w.NEEDS[name], w.OUTPUTS[name], Patient(VP[0]), 1.0)
    sent: list[Message] = []
    pump._uplink = sent.append
    return sched, pump, sent


def _command(pump: Pump, fields: dict[str, Any], t: float = 0.0) -> None:
    for native, value in fields.items():
        pump.on_data(native, Datum(native, value, t))


def test_pump_confirmations_carry_the_invented_device_state() -> None:
    _, pump, sent = _pump("A")
    _command(pump, {"basal": 1.0, "bolus": 2.0, "valid_until": 600.0, "command_id": 1})
    assert sent[0].values == {
        "command_id": 1,
        "delivered": 2.0,
        "status": 0,
        "reservoir": 298.0,
        "battery": 80,
    }
    _, pump, sent = _pump("B")
    _command(pump, {"basal_rate": 1.0, "bolus": 21, "valid_until": 600.0, "command_id": 2})
    assert sent[0].values["delivered"] == 21 and sent[0].values["reservoir_units"] == 298.95
    assert sent[0].values["battery"] == 80 and sent[0].values["status"] == 0


def test_a_command_valid_until_now_does_not_revert_at_once() -> None:
    sched, pump, _ = _pump("A")
    _command(pump, {"basal": 2.0, "bolus": 0.0, "valid_until": 0.0, "command_id": 1})
    sched.run_until(120.0)
    assert pump.patient._basal == pytest.approx(2.0 * 1e6 / 60)


def test_display_throttling_drops_about_half_by_default() -> None:
    sched = Scheduler()
    w = L.world()
    shown = []
    display = Display(
        sched, "CompanionDisplay", w.NEEDS["CompanionDisplay"], w.OUTPUTS["CompanionDisplay"], "x"
    )
    assert display.drop == 0.5 and display.dropped == 0
    display.throttle.append((0.0, 100.0))
    display.on_annunciation = display.on_annunciation  # type: ignore[method-assign]
    for i in range(200):
        display.on_data("glucose", Datum("glucose", 100.0 + i, 0.0))
    sched.run_until(100.0)
    shown = [r for r in display.received if r.native_name == "glucose"]
    assert 60 < display.dropped < 140 and display.dropped + len(shown) == 200
    display.drop = 0.0
    display.on_data("glucose", Datum("glucose", 1.0, 100.0))
    assert display.dropped < 140


# ---- metrics -----------------------------------------------------------------------------------


def test_glucose_outcomes_count_minutes_at_the_thresholds() -> None:
    out = M.glucose_outcomes([53.0, 54.0, 69.0, 70.0, 180.0, 181.0, 250.0, 251.0], 8)
    assert out["below_54_min"] == 1 and out["below_70_min"] == 3
    assert out["tir_70_180_min"] == 2 and out["above_180_min"] == 3 and out["above_250_min"] == 1
    assert out["below_54_share"] == 0.125


def test_overlap_and_wrong_readings() -> None:
    assert M._overlap((0.0, 10.0), (5.0, 20.0)) == 5.0 and M._overlap((0.0, 1.0), (2.0, 3.0)) == 0.0
    assert M.wrong_reading(126.0, 7.0) and not M.wrong_reading(121.0, 120.0)
    assert M.wrong_reading(300.0, 280.0) and not M.wrong_reading(290.0, 280.0)


def test_alarms_are_matched_only_by_a_later_display_entry() -> None:
    raised = [(100.0, "#1 "), (200.0, "#2 "), (300.0, "#3 ")]
    shown = [(50.0, "contract:#2 early"), (150.0, "contract:#1 x"), (800.0, "contract:#2 y")]
    on_time, late, lost = M.classify_alarms(raised, shown, 1000.0)
    assert on_time == 1 and late == [600.0] and lost == [700.0]


def test_detections_are_in_time_order() -> None:
    loop = L.build_loop("bridge-contract", VP[0], S.by_name("out_of_range"))
    loop.run()
    found = M.detections(loop)
    assert len({d[1] for d in found}) > 1  # several sources, interleaved in time
    assert found and [d[0] for d in found] == sorted(d[0] for d in found)


def test_the_critical_record_names_the_bridges_unconfirmed_report() -> None:
    loop = L.build_loop("bridge-contract", VP[0], S.by_name("pump_silent"))
    loop.run()
    assert any("UNCONFIRMED (told by the Bridge)" in r["detail"] for r in M.critical_events(loop))


# ---- scenarios and loop defaults ---------------------------------------------------------------


def test_scenario_defaults_and_boundaries() -> None:
    scenario, patients, _ = S.parse({"example": "insulin-loop", "patients": "VP05"})
    assert scenario.duration_s == 86400.0 and patients == "VP05" and scenario.seed == 1
    S.parse(
        {
            "example": "insulin-loop",
            "faults": [{"kind": "display_throttle", "at": "01:00", "drop": 0}],
        }
    )
    assert L.LoopScenario("x").seed == 1 and L.Fault("k", "01:00").minutes == 0.0


# ---- the conformance checks catch a bad system -------------------------------------------------


def test_item_and_timing_checks_catch_bad_traffic(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = Env._observed("default")
    bad = copy.copy(loop)
    good = loop.platform.accepted
    sent = {t for n in loop.devices if n.startswith("Sensor") for t, _, _ in loop.sensor(n).sent}
    t0 = min(sent)
    extra = [
        (
            9e4,
            Message(
                "GlucoseReading", {"glucose": 600.0, "sensor_status": 1.5, "source_time": t0}, 0.0
            ),
        ),
        (
            9e4,
            Message("GlucoseReading", {"glucose": 7.0, "sensor_status": 0, "source_time": t0}, 0.0),
        ),
        (
            9e4,
            Message("GlucoseReading", {"glucose": 0.0, "sensor_status": 4, "source_time": t0}, 0.0),
        ),
    ]
    platform = copy.copy(loop.platform)
    platform.accepted = good + extra
    bad.system = copy.copy(loop.system)
    bad.system.platform = platform
    monkeypatch.setattr(Env, "_observed", lambda devices: bad)
    results = dict(
        Env.verify(
            [
                "item/GlucoseReading.glucose/range",
                "item/GlucoseReading.sensor_status/encoding",
                "item/GlucoseReading.glucose/units",
                "timing/GlucoseReading/max_age",
            ]
        )
    )
    assert all(results.values()), results
    assert results["timing/GlucoseReading/max_age"].startswith(
        "2 readings"
    )  # flagged ones excluded


def test_claim_checks_catch_a_bridge_without_its_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    real = L.build_loop
    monkeypatch.setattr(Env.L, "build_loop", lambda arch, *a, **k: real("bridge-basic", *a, **k))
    results = dict(Env.verify(["claim/C1", "claim/C2"]))
    assert results["claim/C1"] and "stale" in results["claim/C1"]
    assert results["claim/C2"] == "a bolus was delivered twice"


def test_envelope_limits_are_inclusive() -> None:
    def decide(**declares: Any) -> bool:
        return Env.check(Env.Change("x", "", "unique", "CompanionDisplay", declares)).inside

    assert decide(display_latency_s=600) and not decide(display_latency_s=601)
    assert (
        decide(sample_period_s=60)
        and decide(sample_period_s=300)
        and not decide(sample_period_s=59)
    )


# ---- G7 and G8 report what is wrong ------------------------------------------------------------


def test_g7_reports_a_command_that_ignores_the_failsafe_basal() -> None:
    loop = G._run("x", G.LOSS_STATES["readings stop"])
    assert G.failsafe_problems(loop) == []
    pump = loop.pump("PumpA")
    spans = [e.time for e in loop.bridge.log.events if e.kind == "contract.failsafe"]  # type: ignore[union-attr]
    cid = next(i for i, (t, _, _) in loop.controller.issued.items() if t > spans[0])
    pump.executed.append((spans[0] + 1, cid, 0.0, 9.9))
    assert any("ran basal 9.9" in p for p in G.failsafe_problems(loop))


def test_g8_reports_a_failsafe_without_an_alarm(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = G._run("x", G.LOSS_STATES["readings stop"])
    loop.bridge.contract.alarms.clear()  # type: ignore[union-attr]
    calm = L.build_loop("bridge-contract", VP[0], L.LoopScenario("ok", duration_s=3600.0))
    calm.run()
    calm.bridge.contract.alarms.clear()  # type: ignore[union-attr]
    assert G.visibility_problems(calm) == []  # no fail-safe and no alarm: nothing to report
    assert G.visibility_problems(loop) == ["fail-safe was entered without an alarm"]
    monkeypatch.setattr(G, "failsafe_problems", lambda loop: ["broken"])
    monkeypatch.setattr(G, "visibility_problems", lambda loop: ["broken"])
    world = L.world()
    assert G.g7(world, 1)[0] is False and G.g8(world, 1)[0] is False
    assert "broken" in G.g7(world, 1)[1] and "broken" in G.g8(world, 1)[1]
    monkeypatch.setattr(G, "failsafe_problems", lambda loop: [])
    monkeypatch.setattr(G, "visibility_problems", lambda loop: [])
    assert ";" not in G.g7(world, 1)[1] and ";" not in G.g8(world, 1)[1]


def test_nan_is_not_a_valid_glucose() -> None:
    item = SPEC.role("glucose_value")
    assert item is not None and item.violation(math.nan) is not None


def test_the_bolus_units_check_catches_a_mis_delivered_bolus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = Env._observed("default")
    bad = copy.copy(loop)
    bad.devices = dict(loop.devices)
    pump = copy.copy(loop.pump("PumpA"))
    t, cid, bolus, basal = next(e for e in pump.executed if e[2] > 0)
    pump.executed = [*pump.executed, (t, cid, bolus + 0.5, basal)]
    bad.devices["PumpA"] = pump
    monkeypatch.setattr(Env, "_observed", lambda devices: bad)
    assert dict(Env.verify(["item/DoseCommand.bolus/units"]))["item/DoseCommand.bolus/units"]
